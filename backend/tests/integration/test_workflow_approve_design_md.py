"""Integration test for BEHAVIOR.md §3.5 ``workflow:approve_design_md``.

Exercises the full happy path of the **approve_design_md** workflow
end-to-end through the real FastAPI ``app``. The workflow takes a
draft :class:`DesignDocument` (the postcondition of workflow §3.4,
verified in ``test_workflow_generate_design_md``) and flips it to the
approved state — stamping ``approved_by`` with the ``ri``-role user
who clicked "Schváliť DESIGN.md" and ``approved_at`` with the current
timestamp. The approval then unlocks two downstream effects the
orchestrator performs before returning control to the UI:

    1. The module's ``design_doc_path`` is populated so the
       ``DesignDocViewer`` can round-trip between the DB row and the
       KB file, and so the §3.9 Architect session can locate the
       frozen DESIGN.md for context injection.
    2. If every one of the module's declared dependencies is in
       ``status='done'``, the module itself transitions
       ``in_design → in_development`` — the gate §5.4 and the
       cross-cutting constraint §1.4 "module_dependency_order" spell
       out. When a dependency is still incomplete the DESIGN.md
       approval still succeeds, but the module stays in ``in_design``
       until the dependency is resolved (edge §4.3
       ``edge:module_dependency_not_done`` applied at the
       approve-time boundary).

The filesystem write to
``/home/icc/knowledge/projects/nex-horizont/modules/DOB/DESIGN.md``
is orchestration territory — the test observes the DB-level
signal (``project_modules.design_doc_path`` populated) rather than
asserting an actual file on disk. The real filesystem write is a
side effect the orchestrator performs after the HTTP round-trip this
test drives; its contents are already pinned by the DESIGN.md row
the test approves.

    Precondition (per BEHAVIOR.md §3.5):
        * A :class:`DesignDocument` exists with ``approved_by=NULL``
          (the "draft" state — BEHAVIOR.md §3.5 uses the informal
          ``status='draft'`` label; the physical representation is the
          ``approved_by IS NULL`` column check).
        * The module the document is scoped to exists in
          ``status='in_design'`` (the postcondition of §3.4 — the
          module's entry to the workflow).
        * The actor has role ``ri`` (``ri_director`` Zoltán or
          ``ri_senior`` Tibor per BEHAVIOR.md §1.1).

    Steps (per BEHAVIOR.md §3.5):
        1. Tibor opens the DESIGN.md for review
           → ``GET /api/v1/design-documents/{id}`` returns the draft
           row for the ``DesignDocViewer``.
        2. Tibor makes a technical edit (e.g. adds an index on
           ``purchase_orders.supplier_id`` per the worked example)
           → ``PATCH /api/v1/design-documents/{id}`` with the updated
           ``content`` and a bumped ``version`` ("inkrementuje
           ``version`` pri každom save").
        3. Tibor clicks "Schváliť DESIGN.md" — the UI shows the
           "Po schválení sa dokument zapíše do KB na filesystem"
           confirmation (client-side, not HTTP-observable).
        4. Tibor confirms
           → ``PATCH /api/v1/design-documents/{id}`` with
           ``{"approved_by": <tibor_uuid>}``. The service
           auto-stamps ``approved_at = now()``. The orchestrator then
           writes the DESIGN.md to the KB filesystem and drives two
           PATCHes against ``/api/v1/project-modules/{id}``:
                a. ``{"design_doc_path": "..."}`` — records the file
                   location so the UI and Architect can locate it.
                b. ``{"status": "in_development"}`` — only if every
                   declared dependency is ``done`` (§5.4 gate).

    Postcondition (per BEHAVIOR.md §3.5):
        * ``design_documents`` row has ``approved_by != NULL`` and
          ``approved_at != NULL``.
        * The module's ``design_doc_path`` is populated.
        * The module is in ``status='in_development'`` (conditional
          on dependencies — §5.4 / §1.4).
        * The "Otvoriť Architect session" button activates for DOB —
          modelled as the DB-level precondition the UI gates on
          (``design_documents.approved_by != NULL`` for the module).

At least two edge cases are verified alongside the happy path:

    * **Approval is sticky** — a subsequent PATCH after approval
      (e.g. a late content fix) does **not** re-stamp the original
      ``approved_at``, because the auto-stamp guard in
      :mod:`backend.services.design_document` is keyed on
      ``document.approved_by is None`` at entry. This mirrors the
      equivalent contract for ``professional_specifications`` in
      §3.3 / ``test_workflow_approve_professional_spec`` and is the
      observable side of the §10 pipeline-gating contract — the
      approved state is monotonic.
    * **Module dependency still open at approval time**
      (§4.3 ``edge:module_dependency_not_done`` applied at the §3.5
      boundary) — DESIGN.md approval itself is unconditional, but the
      module transition ``in_design → in_development`` requires every
      declared dependency to be ``done`` (BEHAVIOR.md §1.4 /
      §5.4). When a dependency is still ``in_design`` the
      orchestrator approves the DESIGN.md and sets
      ``design_doc_path`` but leaves the module in ``in_design`` and
      surfaces the blocker to the UI. The row transitions to
      ``in_development`` only after the dependency completes — the
      "Recovery" path §4.3 spells out.

Auth note:
    The current codebase (Feats 0–6) wires routers directly without a
    JWT dependency, so the integration test does not exercise a login
    flow. The "role=ri" precondition is satisfied by persisting the
    approver with ``role='ri'`` and sending their UUID as
    ``approved_by``. Role enforcement at the router level is a
    separate concern covered by future auth-middleware tests.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import (
    ModuleDependency,
    Project,
    ProjectMember,
    ProjectModule,
)
from backend.db.models.specifications import DesignDocument

# ---------------------------------------------------------------------------
# Precondition fixtures — Zoltán (ri_director) / Tibor (ri_senior), the
# NEX Horizont project, the DOB module in ``in_design`` state, and the
# draft DESIGN.md produced by workflow §3.4 that this workflow will
# approve.
# ---------------------------------------------------------------------------


@pytest.fixture()
def zoltan(db_session) -> User:
    """Persist Zoltán — the ``ri_director`` actor from BEHAVIOR.md §1.1."""
    user = User(
        username="zoltan",
        email="zoltan@isnex.ai",
        password_hash="hashed-placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def tibor(db_session) -> User:
    """Persist Tibor — the ``ri_senior`` primary actor in §3.5's worked example.

    BEHAVIOR.md §3.5 names Tibor as the actor throughout the steps
    table. Zoltán (``ri_director``) is an equally valid approver —
    both resolve to ``role='ri'`` at the DB level — but the worked
    example is Tibor's, so the happy-path test follows suit.
    """
    user = User(
        username="tibor",
        email="tibor@isnex.ai",
        password_hash="hashed-placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def nex_horizont(db_session, zoltan) -> Project:
    """Persist the NEX Horizont project.

    Matches the BEHAVIOR.md §3.5 worked example: the DESIGN.md being
    approved belongs to the DOB module of NEX Horizont and, on
    approval, is written to
    ``/home/icc/knowledge/projects/nex-horizont/modules/DOB/DESIGN.md``.
    """
    project = Project(
        name="NEX Horizont",
        slug="nex-horizont",
        category="multimodule",
        description="Enterprise ERP successor to NEX Command.",
        created_by=zoltan.id,
    )
    db_session.add(project)
    db_session.flush()

    db_session.add(ProjectMember(project_id=project.id, user_id=zoltan.id))
    db_session.flush()
    return project


@pytest.fixture()
def dob_module(db_session, nex_horizont) -> ProjectModule:
    """Persist the DOB module in ``in_design`` — the §3.5 entry state.

    BEHAVIOR.md §3.4 postcondition line 3 ("Modul DOB zostáva v stave
    ``in_design``") is §3.5's precondition. The module enters this
    workflow in ``in_design``; a successful approval transitions it
    to ``in_development`` via the orchestrator, gated by every
    declared dependency being ``done``.
    """
    module = ProjectModule(
        project_id=nex_horizont.id,
        code="DOB",
        name="Dodávateľské objednávky",
        category="Nákup",
        status="in_design",
    )
    db_session.add(module)
    db_session.flush()
    return module


# The DESIGN.md markdown the AI would have produced for the DOB module
# in workflow §3.4. Trimmed but recognisable — the test does not
# inspect DESIGN.md content beyond round-tripping it through the
# approval PATCH.
DOB_MODULE_DESIGN_MD = (
    "# DESIGN.md — modul DOB (Dodávateľské objednávky)\n\n"
    "## 1. Data model\n"
    "### 1.1 `purchase_orders`\n"
    "| column        | type         | notes                       |\n"
    "|---------------|--------------|-----------------------------|\n"
    "| id            | UUID PK      | gen_random_uuid()           |\n"
    "| supplier_id   | UUID FK      | → `suppliers.id`            |\n"
    "| status        | varchar(20)  | `draft|approved|sent|done`  |\n"
    "| total_amount  | numeric(12,2)| sum of line totals          |\n\n"
    "### 1.2 `purchase_order_lines`\n"
    "| column            | type         |\n"
    "|-------------------|--------------|\n"
    "| id                | UUID PK      |\n"
    "| purchase_order_id | UUID FK      |\n"
    "| sku               | varchar(64)  |\n"
    "| quantity          | numeric(12,2)|\n"
    "| unit_price        | numeric(12,2)|\n\n"
    "## 2. API endpoints\n"
    "- `POST /api/v1/dob/orders` — create order (draft).\n"
    "- `POST /api/v1/dob/orders/{id}/approve` — `ri`-role approval.\n"
    "- `POST /api/v1/dob/orders/{id}/send` — email the supplier.\n"
    "- `GET  /api/v1/dob/orders/{id}/export` — accounting export.\n\n"
    "## 3. Business rules\n"
    "- Každá objednávka musí byť pre práve jedného dodávateľa\n"
    "  (constraint z profesionálnej špecifikácie).\n"
)

# The §3.5 worked example: "Tibor urobí technické úpravy (napr. pridá
# index na ``purchase_orders.supplier_id``)". Same content as above
# plus the index note — the edit Tibor saves before clicking
# "Schváliť DESIGN.md".
DOB_MODULE_DESIGN_MD_EDITED = DOB_MODULE_DESIGN_MD + (
    "\n## 4. Indexy\n"
    "- `ix_purchase_orders_supplier_id` na "
    "`purchase_orders.supplier_id` — hot path pri liste objednávok\n"
    "  pre dodávateľa.\n"
)

# The filesystem path the orchestrator records on the module after
# the DESIGN.md is written to KB, per BEHAVIOR.md §3.5 step 4
# ("zapíše dokument do
# ``/home/icc/knowledge/projects/nex-horizont/modules/DOB/DESIGN.md``").
DOB_DESIGN_MD_KB_PATH = "/home/icc/knowledge/projects/nex-horizont/modules/DOB/DESIGN.md"


@pytest.fixture()
def draft_design_doc(client, db_session, nex_horizont, dob_module) -> dict[str, Any]:
    """Create a **draft** (un-approved) DESIGN.md for the DOB module.

    Mirrors the postcondition of workflow §3.4 exactly: a
    ``design_documents`` row with ``doc_type='design'``, ``version=1``
    and ``approved_by=NULL``. Created via the real HTTP endpoint so
    the §3.5 test exercises the full router path both on entry
    (``GET`` / ``PATCH``) and sanity-checks that the §3.4
    postcondition is representable at the public API.
    """
    resp = client.post(
        "/api/v1/design-documents",
        json={
            "project_id": str(nex_horizont.id),
            "module_id": str(dob_module.id),
            "doc_type": "design",
            "content": DOB_MODULE_DESIGN_MD,
        },
    )
    assert resp.status_code == 201, resp.text
    row = resp.json()
    # Sanity-check the §3.4 postcondition so a future regression
    # surfaces here, not deep inside the §3.5 assertions.
    assert row["approved_by"] is None
    assert row["approved_at"] is None
    assert row["version"] == 1
    assert row["doc_type"] == "design"
    assert row["module_id"] == str(dob_module.id)
    return row


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.5 end-to-end.
# ---------------------------------------------------------------------------


class TestApproveDesignMdHappyPath:
    """End-to-end walkthrough of workflow §3.5 against the real app."""

    def test_full_workflow_with_technical_edit(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        dob_module,
        draft_design_doc,
    ):
        """Drive steps 1-4 of the workflow for the DOB module.

        The test asserts both the HTTP contract (status codes, payload
        shape) and the database state after each step. The worked
        example from BEHAVIOR.md §3.5 is reproduced faithfully: Tibor
        adds an index on ``purchase_orders.supplier_id`` and bumps
        the version before approving.

        The filesystem write is out of scope at the HTTP layer; the
        test observes the DB-level signal of the write (``module.
        design_doc_path`` populated) instead. The post-approval
        module transition ``in_design → in_development`` is driven
        via the real ``/api/v1/project-modules`` PATCH — the DOB
        module in this test has zero dependencies, so the §5.4 /
        §1.4 gate is open.
        """
        document_id = draft_design_doc["id"]

        # --- Step 1: Tibor opens the DESIGN.md for review. The
        # ``DesignDocViewer`` fetches the draft row for display.
        get_resp = client.get(f"/api/v1/design-documents/{document_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["id"] == document_id
        assert get_resp.json()["content"] == DOB_MODULE_DESIGN_MD
        assert get_resp.json()["approved_by"] is None
        assert get_resp.json()["approved_at"] is None
        assert get_resp.json()["version"] == 1

        # --- Step 2: Tibor makes a technical edit and saves. "Systém
        # ukladá zmeny, inkrementuje ``version`` pri každom save" —
        # the edit PATCH carries both the new content and the bumped
        # version. Approval columns must stay untouched.
        edit_resp = client.patch(
            f"/api/v1/design-documents/{document_id}",
            json={
                "content": DOB_MODULE_DESIGN_MD_EDITED,
                "version": 2,
            },
        )
        assert edit_resp.status_code == 200, edit_resp.text
        edited = edit_resp.json()
        assert edited["content"] == DOB_MODULE_DESIGN_MD_EDITED
        assert edited["version"] == 2
        # Editing content must not silently approve the DESIGN.md.
        assert edited["approved_by"] is None
        assert edited["approved_at"] is None

        # --- Step 3: Tibor clicks "Schváliť DESIGN.md" and sees the
        # "Po schválení sa dokument zapíše do KB na filesystem"
        # confirmation. Client-side only; no HTTP round-trip.
        # --- Step 4: Tibor confirms. Approval is a single PATCH on
        # the design-documents row — the service auto-stamps
        # ``approved_at`` on transition from NULL → user_id.
        before_approval = datetime.now(tz=timezone.utc)
        approve_resp = client.patch(
            f"/api/v1/design-documents/{document_id}",
            json={"approved_by": str(tibor.id)},
            # ``approved_at`` deliberately omitted — the service
            # auto-stamps it on transition from NULL → user_id.
        )
        assert approve_resp.status_code == 200, approve_resp.text
        approved = approve_resp.json()
        assert approved["approved_by"] == str(tibor.id)
        assert approved["approved_at"] is not None
        # Edits from step 2 must survive the approval PATCH.
        assert approved["content"] == DOB_MODULE_DESIGN_MD_EDITED
        assert approved["version"] == 2

        # --- Step 4 (orchestrator): write DESIGN.md to KB and record
        # the path on the module. The filesystem write itself is
        # out of scope at the HTTP layer; the observable contract is
        # the ``project_modules.design_doc_path`` PATCH.
        path_resp = client.patch(
            f"/api/v1/project-modules/{dob_module.id}",
            json={"design_doc_path": DOB_DESIGN_MD_KB_PATH},
        )
        assert path_resp.status_code == 200, path_resp.text
        assert path_resp.json()["design_doc_path"] == DOB_DESIGN_MD_KB_PATH

        # --- Step 4 (orchestrator): transition the module to
        # ``in_development``. Zero dependencies on DOB in this
        # fixture, so the §5.4 / §1.4 gate is open.
        status_resp = client.patch(
            f"/api/v1/project-modules/{dob_module.id}",
            json={"status": "in_development"},
        )
        assert status_resp.status_code == 200, status_resp.text
        assert status_resp.json()["status"] == "in_development"

        # --- Postcondition verification (HTTP) ------------------------
        # Re-read the row via the router to mirror what the UI sees
        # on its next refresh — approval, path and status are all
        # observable together.
        after_doc = client.get(f"/api/v1/design-documents/{document_id}")
        assert after_doc.status_code == 200
        assert after_doc.json()["approved_by"] == str(tibor.id)
        assert after_doc.json()["approved_at"] == approved["approved_at"]
        assert after_doc.json()["content"] == DOB_MODULE_DESIGN_MD_EDITED

        after_module = client.get(f"/api/v1/project-modules/{dob_module.id}")
        assert after_module.status_code == 200
        assert after_module.json()["status"] == "in_development"
        assert after_module.json()["design_doc_path"] == DOB_DESIGN_MD_KB_PATH

        # --- Postcondition verification (DB state) --------------------
        db_session.expire_all()

        # 1. ``design_documents`` row has ``approved_by != NULL`` and
        #    ``approved_at != NULL``. Approval is stamped within
        #    this test window.
        persisted_doc = db_session.get(DesignDocument, uuid.UUID(document_id))
        assert persisted_doc is not None
        assert persisted_doc.approved_by == tibor.id
        assert persisted_doc.approved_at is not None
        assert persisted_doc.approved_at >= before_approval
        assert persisted_doc.approved_at <= datetime.now(tz=timezone.utc)
        # 2. Edit from step 2 is persisted — approval does not
        #    overwrite technical edits.
        assert persisted_doc.content == DOB_MODULE_DESIGN_MD_EDITED
        assert persisted_doc.version == 2
        # 3. Immutable attributes still hold — §3.5 "Data touched"
        #    lists ``design_documents`` and ``project_modules`` and
        #    only the mutable columns change.
        assert persisted_doc.project_id == nex_horizont.id
        assert persisted_doc.module_id == dob_module.id
        assert persisted_doc.doc_type == "design"

        # 4. Module DOB is in ``in_development`` with
        #    ``design_doc_path`` populated. BEHAVIOR.md §3.5
        #    postcondition lines 3 and 4 land on the same row.
        persisted_module = db_session.get(ProjectModule, dob_module.id)
        assert persisted_module is not None
        assert persisted_module.status == "in_development"
        assert persisted_module.design_doc_path == DOB_DESIGN_MD_KB_PATH

        # 5. The "Otvoriť Architect session" button activates for
        #    DOB — the UI gate is the existence of an approved
        #    DESIGN.md row for the module. Model the gate as the
        #    list query an activator would run.
        approved_docs_for_module = client.get(
            "/api/v1/design-documents",
            params={
                "module_id": str(dob_module.id),
                "doc_type": "design",
                "approved_by": str(tibor.id),
            },
        )
        assert approved_docs_for_module.status_code == 200
        approved_ids = [row["id"] for row in approved_docs_for_module.json()["items"]]
        assert document_id in approved_ids

    def test_ri_director_may_also_approve(
        self,
        client,
        db_session,
        zoltan,
        nex_horizont,
        dob_module,
        draft_design_doc,
    ):
        """``ri_director`` (Zoltán) is an equally valid approver per §3.5.

        BEHAVIOR.md §3.5 lists the actor as "[[actor:ri_director]]
        alebo [[actor:ri_senior]]" — both roles resolve to
        ``role='ri'`` at the DB level. The router accepts any user
        with that role as ``approved_by``; enforcement is identical
        regardless of which specific ``ri`` user does the click.
        Tibor is the worked-example actor covered above; this test
        pins Zoltán's equivalence.
        """
        document_id = draft_design_doc["id"]

        resp = client.patch(
            f"/api/v1/design-documents/{document_id}",
            json={"approved_by": str(zoltan.id)},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["approved_by"] == str(zoltan.id)
        assert resp.json()["approved_at"] is not None

        db_session.expire_all()
        persisted = db_session.get(DesignDocument, uuid.UUID(document_id))
        assert persisted is not None
        assert persisted.approved_by == zoltan.id


# ---------------------------------------------------------------------------
# Edge cases.
# ---------------------------------------------------------------------------


class TestApproveDesignMdEdgeCases:
    """Edge cases for the ``approve_design_md`` workflow.

    BEHAVIOR.md §3.5 does not list a dedicated ``§4.X`` edge under
    ``approve_design_md`` itself, but two cross-cutting contracts
    apply at this boundary and are worth pinning:

    1. Approval is **monotonic / sticky** — a post-approval PATCH
       (e.g. a late typo fix) must not re-stamp ``approved_at``.
       This is the direct analogue of the §3.3 contract exercised in
       ``test_workflow_approve_professional_spec``; the auto-stamp
       guard in :mod:`backend.services.design_document.update` is
       keyed on ``document.approved_by is None`` at entry, so a
       second transition cannot silently update the stamp.
    2. The module transition ``in_design → in_development``
       triggered by step 4 is gated by §1.4 /
       §5.4 ("all dependencies must be ``done``"). BEHAVIOR.md §3.5
       postcondition line 4 spells this out verbatim: "Modul DOB:
       ``status='in_development'`` **(ak sú závislosti splnené)**".
       Edge §4.3 ``edge:module_dependency_not_done`` formalises the
       blocker; it is reused here at the approve-time boundary. The
       DESIGN.md itself still gets approved — the gate blocks only
       the module transition.
    """

    def test_approval_is_sticky_on_subsequent_patch(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        dob_module,
        draft_design_doc,
    ):
        """A post-approval content fix does NOT re-stamp ``approved_at``.

        Once a DESIGN.md has ``approved_by != NULL``, subsequent
        PATCHes (e.g. a typo fix) must not overwrite the original
        ``approved_at`` — the auto-stamp guard is keyed on
        ``document.approved_by is None`` at entry. This is the
        observable side of the §10 pipeline-gating contract: the
        approved state is monotonic, and the stamp marks when the
        ``ri`` user originally clicked "Schváliť DESIGN.md".

        (An explicit un-approval flow is not modelled by this
        service — it belongs to admin tooling.)
        """
        document_id = draft_design_doc["id"]

        # --- First approval — stamps ``approved_at`` automatically.
        first = client.patch(
            f"/api/v1/design-documents/{document_id}",
            json={"approved_by": str(tibor.id)},
        )
        assert first.status_code == 200, first.text
        original_approved_at = first.json()["approved_at"]
        assert original_approved_at is not None

        # --- Late content fix — Tibor corrects a typo in a business
        # rule. The router accepts the PATCH, but the approval stamp
        # must not slide forward.
        fix = DOB_MODULE_DESIGN_MD_EDITED + "\n<!-- late typo fix -->\n"
        second = client.patch(
            f"/api/v1/design-documents/{document_id}",
            json={"content": fix},
        )
        assert second.status_code == 200, second.text
        assert second.json()["content"] == fix
        # Approval columns unchanged.
        assert second.json()["approved_by"] == str(tibor.id)
        assert second.json()["approved_at"] == original_approved_at

        db_session.expire_all()
        persisted = db_session.get(DesignDocument, uuid.UUID(document_id))
        assert persisted is not None
        assert persisted.approved_by == tibor.id
        # Compare via parsed ``datetime`` — the JSON encoder emits
        # trailing ``Z`` while ``datetime.isoformat()`` emits
        # ``+00:00``; both denote the same UTC instant.
        assert persisted.approved_at is not None
        assert persisted.approved_at == datetime.fromisoformat(original_approved_at.replace("Z", "+00:00"))

    def test_module_dependency_not_done_blocks_in_development_transition(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        dob_module,
        draft_design_doc,
    ):
        """§4.3 applied at §3.5 — approval succeeds, module transition waits.

        BEHAVIOR.md §1.4 / §5.4: a module may only enter
        ``in_development`` once every declared dependency is
        ``done``. §3.5 postcondition line 4 makes the conditional
        explicit ("ak sú závislosti splnené"). When a dependency
        is still open at approve time:

        * The DESIGN.md row is still approved — approval is an
          unconditional document-level action.
        * ``project_modules.design_doc_path`` is still populated —
          the file has been written to KB regardless.
        * The module **stays** in ``in_design`` — the transition is
          the gated side of the workflow.
        * On recovery (the dependency completes) the same transition
          becomes permissible. The test exercises the recovery path
          §4.3 spells out ("Tibor dokončí GSC najprv → potom môže
          STK na ``in_development``") by flipping the dependency to
          ``done`` mid-test and observing the module transition
          succeed.
        """
        document_id = draft_design_doc["id"]

        # --- Seed a dependency: DOB depends on GSC (``planned`` →
        # not ``done``). The relationship is expressed through the
        # ``module_dependencies`` table; GSC is any module in the
        # same project that has not yet completed.
        gsc = ProjectModule(
            project_id=nex_horizont.id,
            code="GSC",
            name="Globálne skladové karty",
            category="Sklad",
            status="in_design",
        )
        db_session.add(gsc)
        db_session.flush()
        db_session.add(
            ModuleDependency(
                module_id=dob_module.id,
                depends_on_module_id=gsc.id,
            )
        )
        db_session.flush()

        # --- Step 4 (approval): unconditional, still succeeds.
        approve_resp = client.patch(
            f"/api/v1/design-documents/{document_id}",
            json={"approved_by": str(tibor.id)},
        )
        assert approve_resp.status_code == 200, approve_resp.text
        assert approve_resp.json()["approved_by"] == str(tibor.id)
        assert approve_resp.json()["approved_at"] is not None

        # --- Step 4 (orchestrator): write to KB → set path. This is
        # the filesystem-adjacent side effect, still unconditional.
        path_resp = client.patch(
            f"/api/v1/project-modules/{dob_module.id}",
            json={"design_doc_path": DOB_DESIGN_MD_KB_PATH},
        )
        assert path_resp.status_code == 200, path_resp.text

        # --- Step 4 (orchestrator): transition to ``in_development``.
        # The §5.4 / §1.4 gate is closed (GSC is not ``done``). A
        # correctly-gated orchestrator therefore does NOT issue the
        # status PATCH — the observable side of the block is the
        # module staying in ``in_design`` with the dependency
        # visible to the UI.
        deps_check = client.get(
            "/api/v1/module-dependencies",
            params={"module_id": str(dob_module.id)},
        )
        assert deps_check.status_code == 200, deps_check.text
        blocker_ids = {row["depends_on_module_id"] for row in deps_check.json()["items"]}
        assert str(gsc.id) in blocker_ids

        # GSC is not ``done`` → gate closed → no transition.
        dep_state = client.get(f"/api/v1/project-modules/{gsc.id}")
        assert dep_state.status_code == 200
        assert dep_state.json()["status"] != "done"

        # DB invariants during the blocked window.
        db_session.expire_all()
        persisted_doc = db_session.get(DesignDocument, uuid.UUID(document_id))
        assert persisted_doc is not None
        assert persisted_doc.approved_by == tibor.id
        persisted_module = db_session.get(ProjectModule, dob_module.id)
        assert persisted_module is not None
        assert persisted_module.status == "in_design"
        assert persisted_module.design_doc_path == DOB_DESIGN_MD_KB_PATH

        # --- Recovery (§4.3): dependency completes, gate opens, the
        # same transition now succeeds. BEHAVIOR.md §4.3 "Tibor
        # dokončí GSC najprv → potom môže STK na ``in_development``"
        # is the template; applied here, closing GSC lets DOB
        # advance on the next orchestration tick.
        gsc_done = client.patch(
            f"/api/v1/project-modules/{gsc.id}",
            json={"status": "done"},
        )
        assert gsc_done.status_code == 200, gsc_done.text
        assert gsc_done.json()["status"] == "done"

        transition = client.patch(
            f"/api/v1/project-modules/{dob_module.id}",
            json={"status": "in_development"},
        )
        assert transition.status_code == 200, transition.text
        assert transition.json()["status"] == "in_development"

        db_session.expire_all()
        persisted_module = db_session.get(ProjectModule, dob_module.id)
        assert persisted_module is not None
        assert persisted_module.status == "in_development"
        # Path stays set — recovery does not re-write it.
        assert persisted_module.design_doc_path == DOB_DESIGN_MD_KB_PATH
        # Approval stamp is still the original — the recovery path
        # does not re-touch the document row.
        persisted_doc = db_session.get(DesignDocument, uuid.UUID(document_id))
        assert persisted_doc is not None
        assert persisted_doc.approved_by == tibor.id

    def test_approving_nonexistent_document_returns_404(
        self,
        client,
        db_session,
        tibor,
    ):
        """PATCH on a fabricated UUID → 404, no DB side effect.

        A stale browser tab or a direct API hit with a wrong UUID
        must not corrupt the database. The router's
        ``_map_value_error`` translates the service-layer
        ``ValueError("... not found")`` into an HTTP 404 and rolls
        back the session (no row is created / touched).
        """
        fabricated = uuid.uuid4()

        resp = client.patch(
            f"/api/v1/design-documents/{fabricated}",
            json={"approved_by": str(tibor.id)},
        )
        assert resp.status_code == 404, resp.text
        assert "not found" in resp.json()["detail"].lower()

        db_session.expire_all()
        assert db_session.get(DesignDocument, fabricated) is None

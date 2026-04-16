"""Integration test for BEHAVIOR.md §3.4 ``workflow:generate_design_md``.

Exercises the full happy path of the **generate_design_md** workflow
end-to-end through the real FastAPI ``app``. The workflow takes an
approved :class:`ProfessionalSpecification` (the postcondition of
workflow §3.3, verified in ``test_workflow_approve_professional_spec``)
and produces a draft :class:`DesignDocument` with ``doc_type='design'``,
``version=1`` and ``approved_by=NULL`` — the editable DESIGN.md that the
``ri`` user will review and approve in workflow §3.5. The AI /
Anthropic streaming call that actually synthesises the markdown is out
of scope at this layer and is treated as a black box; the test
verifies the *observable* side effects (the HTTP contract, the DB
row, and the module-status invariant from the §3.4 postcondition).

    Precondition (per BEHAVIOR.md §3.4):
        * A :class:`ProfessionalSpecification` exists with
          ``approved_by != NULL`` (the postcondition of workflow §3.3).
        * For module-level generation: a Foundation DESIGN.md (a
          :class:`DesignDocument` with ``module_id IS NULL`` and
          ``doc_type='design'``) exists and is approved — DESIGN.md
          §1.5 "Foundation DESIGN.md == ``module_id IS NULL AND
          doc_type='design'``".
        * Actor has role ``ri`` (``ri_director`` Zoltán or
          ``ri_senior`` Tibor per BEHAVIOR.md §1.1).

    Steps (per BEHAVIOR.md §3.4):
        1. Zoltán clicks "Generovať DESIGN.md" next to the approved
           professional spec → UI offers "Foundation dokument" or
           "Modul: [výber modulu]".
        2. Zoltán picks "Modul: DOB" → system assembles the
           context (professional spec + Foundation DESIGN.md + module
           metadata). At the HTTP layer the orchestrator pre-flights
           this by GET-ing the approved spec
           (``/api/v1/professional-specifications/{id}``) and the
           Foundation DESIGN.md
           (``/api/v1/design-documents?project_id=...&module_id=None&doc_type=design``).
        3. Zoltán confirms generation → AI streams the DESIGN.md
           content (black-boxed at this layer — the test supplies the
           markdown the AI would produce for the DOB module).
        4. System persists the DESIGN.md row as a
           :class:`DesignDocument`
           → ``POST /api/v1/design-documents`` with ``doc_type='design'``,
           ``version=1``, ``approved_by=NULL``.
        5. Zoltán sees the generated DESIGN.md in an editable view —
           modelled as ``GET /api/v1/design-documents/{id}``.

    Postcondition (per BEHAVIOR.md §3.4):
        * A :class:`DesignDocument` exists with ``module_id=dob_id``,
          ``doc_type='design'`` and ``version=1``.
        * The document is editable before approval
          (``approved_by IS NULL``, ``approved_at IS NULL``).
        * Module DOB remains in ``status='in_design'`` — generating
          DESIGN.md does **not** auto-advance the module. The
          ``in_development`` transition happens in workflow §3.5 on
          approval (see BEHAVIOR.md §5.4 / §5.5 state machines).

At least one edge case is verified alongside the happy path:

    * **Foundation DESIGN.md generation** (BEHAVIOR.md §3.4 step 1
      alternative) — the same workflow produces a project-level
      document when the user picks "Foundation dokument". The row
      has ``module_id IS NULL`` and is recognised as the Foundation
      DESIGN.md by the DESIGN.md §1.5 query (``module_id IS NULL AND
      doc_type='design'``). This is the second happy-path variation
      explicitly named in step 1 of the workflow.
    * **§4.2 ``edge:design_approval_without_spec_approval``** — an
      attempt to generate DESIGN.md from a draft (un-approved)
      professional spec must be rejected. At the CRUD layer of
      Feats 0–6 the backend has no dedicated
      ``generate_design_md`` orchestration endpoint, so the gate is
      exercised via the observable pre-flight the orchestrator runs:
      the ``approved_by``-filtered list query is the signal the UI
      and orchestrator use to enable / disable the "Generovať
      DESIGN.md" button. With a draft prof spec the filtered list
      returns empty, so a correctly-gated orchestrator refuses the
      generation and no :class:`DesignDocument` is created — the
      postcondition of §4.2 ("systém odmietne request, neurobí
      design_document"). The "approved state is monotonic" contract
      of §3.3 flips the gate open again once approval lands.

Auth note:
    The current codebase (Feats 0–6) wires routers directly without a
    JWT dependency, so the integration test does not exercise a login
    flow. The "role=ri" precondition is satisfied by persisting the
    generating user with ``role='ri'``. Role enforcement at the router
    level is a separate concern covered by future auth-middleware
    tests.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project, ProjectMember, ProjectModule
from backend.db.models.specifications import (
    DesignDocument,
    ProfessionalSpecification,
    RawSpecification,
)

# ---------------------------------------------------------------------------
# Precondition fixtures — Zoltán (ri_director) / Tibor (ri_senior), the
# NEX Horizont project, the DOB module, the approved Foundation DESIGN.md
# and the approved professional specification for DOB (postcondition of
# workflow §3.3).
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
    """Persist Tibor — the ``ri_senior`` alternative generator (BEHAVIOR.md §1.1).

    BEHAVIOR.md §3.4 names both Zoltán (``ri_director``) and Tibor
    (``ri_senior``) as valid actors. The fixture lets a happy-path
    variation cover Tibor generating in Zoltán's stead — same role
    (``ri``), same contract.
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
    """Persist the NEX Horizont project and add Zoltán as a member.

    Matches the BEHAVIOR.md §3.4 worked example: Zoltán generates the
    DESIGN.md for the DOB module of NEX Horizont from the approved
    professional spec and the Foundation DESIGN.md.
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
    """Persist the DOB (Dodávateľské objednávky) module in ``in_design`` state.

    BEHAVIOR.md §3.4 postcondition line 3: "Modul DOB zostáva v stave
    ``in_design``" — the module is already in_design at the start of
    the workflow (it entered that state when its own sub-spec was
    opened for design work) and **must not** transition during DESIGN.md
    generation. The transition to ``in_development`` happens in
    workflow §3.5 on approval, gated by the §5.4 state machine.
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


# Minimal but representative Foundation DESIGN.md — the context the
# system injects for module-level generation per BEHAVIOR.md §3.4 step
# 2 ("profesionálna spec + Foundation DESIGN.md + modul kontext").
NEX_HORIZONT_FOUNDATION_DESIGN = (
    "# NEX Horizont — Foundation DESIGN.md\n\n"
    "## 1. Architecture overview\n"
    "NEX Horizont is a multimodule ERP. Every module owns its own\n"
    "PostgreSQL schema and exposes a REST API under `/api/v1/<module>`.\n\n"
    "## 2. Cross-cutting concerns\n"
    "- Authentication: JWT with `ri` / `junior` roles.\n"
    "- Database driver: pg8000 (sync).\n"
    "- Module dependencies declared in `module_dependencies` — a\n"
    "  module may only enter `in_development` when all its\n"
    "  dependencies are `done`.\n"
)


@pytest.fixture()
def foundation_design_doc(db_session, nex_horizont, zoltan) -> DesignDocument:
    """Seed an **approved** Foundation DESIGN.md for NEX Horizont.

    BEHAVIOR.md §3.4 precondition line 2: "Pre modul: Foundation
    DESIGN.md existuje a je schválený." The row has ``module_id IS
    NULL`` and ``doc_type='design'`` — the query DESIGN.md §1.5
    specifies as the Foundation-DESIGN.md identity. ``approved_by`` is
    set so the module-level generation gate holds.
    """
    doc = DesignDocument(
        project_id=nex_horizont.id,
        module_id=None,
        doc_type="design",
        content=NEX_HORIZONT_FOUNDATION_DESIGN,
        version=1,
        approved_by=zoltan.id,
        approved_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(doc)
    db_session.flush()
    return doc


# Professional specification markdown — the AI's output for the ANDROS
# DOB email, approved by Zoltán in workflow §3.3 (worked example of
# BEHAVIOR.md §3.3, "Konkrétny príklad").
ANDROS_DOB_PROFESSIONAL_SPEC = (
    "# Profesionálna špecifikácia — modul DOB (Dodávateľské objednávky)\n\n"
    "## Business Requirements\n"
    "1. Automatické generovanie objednávok z webshop zákaziek.\n"
    "2. Email notifikácie dodávateľom po odoslaní objednávky.\n"
    "3. Schvaľovanie objednávok cez viacstupňový workflow.\n"
    "4. Napojenie na účtovníctvo cez exportný súbor.\n\n"
    "## Aktori\n"
    "- Nákupca (vytvára a odosiela objednávky)\n"
    "- Skladník (prijíma dodávky, potvrdzuje príjem)\n"
    "- Schvaľovateľ (ri): odsúhlasuje objednávky nad limitom\n\n"
    "## Use Cases\n"
    "- UC-01 Vytvorenie objednávky pre jedného dodávateľa\n"
    "- UC-02 Viacstupňové schválenie objednávky\n"
    "- UC-03 Export schválenej objednávky do účtovníctva\n\n"
    "## Constraints\n"
    "- Objednávka musí byť pre jedného dodávateľa.\n"
    "- Exportný súbor podľa špecifikácie účtovného systému.\n\n"
    "## Out of Scope\n"
    "- Platobné brány — nie je súčasťou modulu DOB.\n"
)

# Mirrors the AI-generated DESIGN.md for the DOB module — the
# "Konkrétny príklad" in BEHAVIOR.md §3.4 names ``purchase_orders``,
# ``purchase_order_lines``, API endpoints, DB schema. A trimmed but
# recognisable version is sufficient for the integration contract —
# the test does not inspect DESIGN.md content beyond round-tripping.
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


@pytest.fixture()
def approved_raw_spec(db_session, nex_horizont, zoltan) -> RawSpecification:
    """Seed a ``done`` raw specification — the parent of the prof spec.

    Seeding the parent row on the session directly (not via HTTP)
    matches the approach of ``test_workflow_approve_professional_spec``
    — the raw-spec lifecycle is covered by workflows §3.1 / §3.2 and
    is not the subject of this test.
    """
    raw = RawSpecification(
        project_id=nex_horizont.id,
        input_text="ANDROS DOB email — covered by workflow §3.1.",
        input_format="text",
        status="done",
        created_by=zoltan.id,
    )
    db_session.add(raw)
    db_session.flush()
    return raw


@pytest.fixture()
def approved_professional_spec(client, db_session, nex_horizont, zoltan, approved_raw_spec) -> dict[str, Any]:
    """Create an **approved** professional specification via HTTP.

    Mirrors the postcondition of workflow §3.3 exactly: a professional
    spec row with ``approved_by=<zoltan.id>`` and ``approved_at``
    stamped. The approval is driven through the real PATCH endpoint
    to pin the HTTP-layer contract the orchestrator relies on when
    pre-flighting the §3.4 precondition.
    """
    create = client.post(
        "/api/v1/professional-specifications",
        json={
            "raw_spec_id": str(approved_raw_spec.id),
            "project_id": str(nex_horizont.id),
            "content": ANDROS_DOB_PROFESSIONAL_SPEC,
        },
    )
    assert create.status_code == 201, create.text
    spec = create.json()
    approve = client.patch(
        f"/api/v1/professional-specifications/{spec['id']}",
        json={"approved_by": str(zoltan.id)},
    )
    assert approve.status_code == 200, approve.text
    approved = approve.json()
    # Sanity-check the precondition so a future regression in §3.3
    # surfaces here, not deep inside the §3.4 assertions.
    assert approved["approved_by"] == str(zoltan.id)
    assert approved["approved_at"] is not None
    return approved


@pytest.fixture()
def draft_professional_spec(client, db_session, nex_horizont, zoltan, approved_raw_spec) -> dict[str, Any]:
    """Create a **draft** (un-approved) professional specification.

    Used by the §4.2 edge case: a correctly-gated orchestrator must
    refuse DESIGN.md generation when the precondition query for an
    approved spec returns empty.
    """
    resp = client.post(
        "/api/v1/professional-specifications",
        json={
            "raw_spec_id": str(approved_raw_spec.id),
            "project_id": str(nex_horizont.id),
            "content": ANDROS_DOB_PROFESSIONAL_SPEC,
        },
    )
    assert resp.status_code == 201, resp.text
    spec = resp.json()
    assert spec["approved_by"] is None
    return spec


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.4 end-to-end.
# ---------------------------------------------------------------------------


class TestGenerateDesignMdHappyPath:
    """End-to-end walkthrough of workflow §3.4 against the real app."""

    def test_full_workflow_module_level_generation(
        self,
        client,
        db_session,
        zoltan,
        nex_horizont,
        dob_module,
        foundation_design_doc,
        approved_professional_spec,
    ):
        """Drive steps 1-5 of the workflow for the DOB module.

        The test asserts both the HTTP contract (status codes, payload
        shape) and the database state after each step. The AI call is
        black-boxed — the test supplies the markdown the AI would have
        produced for the DOB professional spec + Foundation DESIGN.md
        context pair.
        """
        spec_id = approved_professional_spec["id"]

        # --- Step 1: Zoltán clicks "Generovať DESIGN.md". The UI
        # confirms the spec is approved (the §3.4 precondition) — we
        # model this as a re-fetch of the spec to prove the gate is
        # open at HTTP level.
        spec_check = client.get(f"/api/v1/professional-specifications/{spec_id}")
        assert spec_check.status_code == 200, spec_check.text
        assert spec_check.json()["approved_by"] == str(zoltan.id)
        assert spec_check.json()["approved_at"] is not None

        # The UI then offers "Foundation dokument" or "Modul: [výber]".
        # Picking "Modul: DOB" means the orchestrator must also verify
        # the Foundation DESIGN.md exists and is approved — DESIGN.md
        # §1.5 "Foundation DESIGN.md == ``module_id IS NULL AND
        # doc_type='design'``".
        foundation_check = client.get(
            "/api/v1/design-documents",
            params={
                "project_id": str(nex_horizont.id),
                "doc_type": "design",
                "approved_by": str(zoltan.id),
            },
        )
        assert foundation_check.status_code == 200, foundation_check.text
        foundation_rows = foundation_check.json()["items"]
        # Exactly one approved design doc exists at this point — the
        # Foundation (seeded by the fixture). The per-module DESIGN.md
        # is what this test is about to create.
        assert [row["id"] for row in foundation_rows] == [str(foundation_design_doc.id)]
        assert foundation_rows[0]["module_id"] is None

        # --- Step 2 (implicit): system assembles context. The
        # context-assembly is a pure read of the rows we just fetched
        # plus the module metadata — verify the DOB module is visible
        # in its precondition state (``in_design``).
        module_check = client.get(f"/api/v1/project-modules/{dob_module.id}")
        assert module_check.status_code == 200, module_check.text
        assert module_check.json()["status"] == "in_design"

        # --- Step 3: Zoltán confirms — AI streams the DESIGN.md.
        # Black-boxed here; the test supplies the markdown the AI
        # would have produced.

        # --- Step 4: system persists the design_document row. Step 4
        # is where the §3.4 postcondition is established: the row has
        # ``doc_type='design'``, ``version=1``, ``approved_by=NULL``.
        create_resp = client.post(
            "/api/v1/design-documents",
            json={
                "project_id": str(nex_horizont.id),
                "module_id": str(dob_module.id),
                "doc_type": "design",
                "content": DOB_MODULE_DESIGN_MD,
                # ``version`` omitted — §3.4 postcondition pins it to
                # 1 via the DB / schema default.
                # ``approved_by`` / ``approved_at`` omitted — §3.4
                # explicitly requires the draft state.
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        created = create_resp.json()
        assert created["project_id"] == str(nex_horizont.id)
        assert created["module_id"] == str(dob_module.id)
        assert created["doc_type"] == "design"
        assert created["content"] == DOB_MODULE_DESIGN_MD
        assert created["version"] == 1
        # The §3.4 postcondition: "dokument je editovateľný pred
        # schválením" — ``approved_by`` / ``approved_at`` must stay
        # ``None`` right after generation.
        assert created["approved_by"] is None
        assert created["approved_at"] is None
        assert created["id"]
        assert created["created_at"]
        assert created["updated_at"]

        # --- Step 5: Zoltán sees the generated DESIGN.md in an
        # editable view — the SpecificationViewer / DesignDocViewer
        # fetches the new row for display.
        view = client.get(f"/api/v1/design-documents/{created['id']}")
        assert view.status_code == 200, view.text
        assert view.json()["id"] == created["id"]
        assert view.json()["content"] == DOB_MODULE_DESIGN_MD
        assert view.json()["approved_by"] is None

        # --- Postcondition verification (DB state) --------------------
        db_session.expire_all()

        # 1. design_documents row exists for the DOB module with
        #    doc_type='design' and version=1.
        persisted = db_session.get(DesignDocument, uuid.UUID(created["id"]))
        assert persisted is not None
        assert persisted.project_id == nex_horizont.id
        assert persisted.module_id == dob_module.id
        assert persisted.doc_type == "design"
        assert persisted.version == 1
        # 2. "Dokument je editovateľný pred schválením" — approval
        #    columns are NULL.
        assert persisted.approved_by is None
        assert persisted.approved_at is None
        # created_at / updated_at are server-stamped via ``func.now()``
        # (the transaction-start timestamp) and are always populated
        # on a successful INSERT — the exact instant is owned by the
        # DB, not the test.
        assert persisted.created_at is not None
        assert persisted.updated_at is not None
        # 3. Content survives the round-trip.
        assert persisted.content == DOB_MODULE_DESIGN_MD

        # 4. Module DOB stays in ``in_design``. BEHAVIOR.md §3.4
        #    postcondition line 3 is explicit: generating DESIGN.md
        #    does NOT auto-advance the module — the transition to
        #    ``in_development`` is owned by workflow §3.5 on approval.
        persisted_module = db_session.get(ProjectModule, dob_module.id)
        assert persisted_module is not None
        assert persisted_module.status == "in_design"
        # ``design_doc_path`` also stays unset — it's written on
        # approval in workflow §3.5, not on generation.
        assert persisted_module.design_doc_path is None

        # 5. The approved professional spec is untouched — workflow
        #    §3.4 reads it but does not mutate it ("Data touched"
        #    lists both tables, but only design_documents is written).
        persisted_spec = db_session.get(ProfessionalSpecification, uuid.UUID(spec_id))
        assert persisted_spec is not None
        assert persisted_spec.approved_by == zoltan.id
        assert persisted_spec.content == ANDROS_DOB_PROFESSIONAL_SPEC

        # 6. The new per-module DESIGN.md is discoverable alongside
        #    the Foundation — the DesignDocViewer lists both when the
        #    project is opened (newest first by ``created_at``).
        project_docs = client.get(
            "/api/v1/design-documents",
            params={"project_id": str(nex_horizont.id), "doc_type": "design"},
        )
        assert project_docs.status_code == 200
        project_doc_ids = [row["id"] for row in project_docs.json()["items"]]
        assert created["id"] in project_doc_ids
        assert str(foundation_design_doc.id) in project_doc_ids
        # And the module-filtered query returns only the per-module
        # DESIGN.md (the Foundation has ``module_id IS NULL`` and is
        # filtered out).
        module_docs = client.get(
            "/api/v1/design-documents",
            params={"module_id": str(dob_module.id)},
        )
        assert module_docs.status_code == 200
        assert [row["id"] for row in module_docs.json()["items"]] == [created["id"]]

    def test_foundation_dokument_alternative(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        approved_professional_spec,
    ):
        """Step 1 alternative — "Foundation dokument" produces a project-level doc.

        BEHAVIOR.md §3.4 step 1 names two alternatives: "Foundation
        dokument" OR "Modul: [výber modulu]". When the user picks
        "Foundation dokument" the workflow produces a :class:`DesignDocument`
        with ``module_id IS NULL`` — the exact query shape DESIGN.md
        §1.5 specifies for the Foundation identity. The professional
        spec precondition is the same (``approved_by != NULL``); the
        per-module Foundation-DESIGN.md precondition only applies to
        module-level generation.
        """
        foundation_markdown = (
            "# NEX Horizont — Foundation DESIGN.md (regenerated)\n\n"
            "## 1. Architecture\n"
            "The Foundation document captures cross-cutting decisions.\n"
        )

        resp = client.post(
            "/api/v1/design-documents",
            json={
                "project_id": str(nex_horizont.id),
                # ``module_id`` deliberately omitted → NULL → Foundation.
                "doc_type": "design",
                "content": foundation_markdown,
            },
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["module_id"] is None
        assert created["doc_type"] == "design"
        assert created["version"] == 1
        assert created["approved_by"] is None
        assert created["approved_at"] is None

        # The DESIGN.md §1.5 identity query ("Foundation DESIGN.md ==
        # ``module_id IS NULL AND doc_type='design'``") returns the
        # row — this is the query the Architect context injection
        # runs to load the Foundation for any module.
        listing = client.get(
            "/api/v1/design-documents",
            params={
                "project_id": str(nex_horizont.id),
                "doc_type": "design",
            },
        )
        assert listing.status_code == 200
        foundation_rows = [row for row in listing.json()["items"] if row["module_id"] is None]
        assert created["id"] in [row["id"] for row in foundation_rows]

        db_session.expire_all()
        persisted = db_session.get(DesignDocument, uuid.UUID(created["id"]))
        assert persisted is not None
        assert persisted.module_id is None
        assert persisted.doc_type == "design"
        assert persisted.approved_by is None


# ---------------------------------------------------------------------------
# Edge cases.
# ---------------------------------------------------------------------------


class TestGenerateDesignMdEdgeCases:
    """Edge cases for the ``generate_design_md`` workflow.

    BEHAVIOR.md §4.2 ``edge:design_approval_without_spec_approval`` is
    the canonical edge for this workflow — generating DESIGN.md without
    an approved professional spec must be rejected. The contract is
    modelled in three observable parts at this layer:

    1. The gate query a correctly-built orchestrator would run before
       starting generation returns zero rows when the prof spec is a
       draft — so the orchestrator refuses the call and no
       ``design_documents`` row is created.
    2. Approving the spec flips the gate open (§3.3 sets
       ``approved_by``; the same filter then returns the row).
    3. Running the generation under the closed gate (i.e. not POSTing
       to ``/api/v1/design-documents``) results in no state change —
       the ``design_documents`` list stays empty for the project.
    """

    def test_unapproved_spec_gates_generation(
        self,
        client,
        db_session,
        zoltan,
        nex_horizont,
        dob_module,
        foundation_design_doc,
        draft_professional_spec,
    ):
        """§4.2 — a draft prof spec fails the pre-flight approval check.

        The "Generovať DESIGN.md" button is disabled in the UI
        whenever ``prof_spec.approved_by IS NULL`` (BEHAVIOR.md §4.2
        "Tlačidlo ... je v UI disabled pokiaľ spec nie je schválená").
        At the HTTP layer this surfaces as an empty response for the
        approved-specs list query — an orchestrator that enforces the
        gate sees zero approved specs for the project and refuses to
        generate. The observable side effect of the refusal is the
        absence of a new ``design_documents`` row for the module.
        """
        # --- Pre-flight (step 1 of the workflow): list approved
        # professional specs for this project. The draft spec has
        # ``approved_by IS NULL`` so it MUST NOT show up — the gate
        # is closed.
        pre_flight = client.get(
            "/api/v1/professional-specifications",
            params={
                "project_id": str(nex_horizont.id),
                "approved_by": str(zoltan.id),
            },
        )
        assert pre_flight.status_code == 200, pre_flight.text
        assert pre_flight.json()["total"] == 0
        assert pre_flight.json()["items"] == []

        # Sanity: the draft spec IS visible via the unfiltered list —
        # the gate is scoped to the approval filter, not a wholesale
        # "doesn't exist".
        unfiltered = client.get(
            "/api/v1/professional-specifications",
            params={"project_id": str(nex_horizont.id)},
        )
        assert unfiltered.status_code == 200
        unfiltered_ids = [row["id"] for row in unfiltered.json()["items"]]
        assert draft_professional_spec["id"] in unfiltered_ids

        # --- A correctly-gated orchestrator therefore does NOT POST
        # to /api/v1/design-documents. Verify the absence of a new
        # DESIGN.md for the DOB module — the Foundation is the only
        # pre-existing row and no module-level DESIGN.md exists.
        module_docs = client.get(
            "/api/v1/design-documents",
            params={"module_id": str(dob_module.id)},
        )
        assert module_docs.status_code == 200
        assert module_docs.json()["total"] == 0
        assert module_docs.json()["items"] == []

        db_session.expire_all()
        module_doc_count = db_session.query(DesignDocument).filter(DesignDocument.module_id == dob_module.id).count()
        assert module_doc_count == 0

        # --- Flip the gate open via §3.3 approval and confirm the
        # same pre-flight now returns the row — generation becomes
        # permissible. This is the "Recovery" path BEHAVIOR.md §4.2
        # spells out: "Zoltán schváli spec → tlačidlo sa aktivuje".
        approve = client.patch(
            f"/api/v1/professional-specifications/{draft_professional_spec['id']}",
            json={"approved_by": str(zoltan.id)},
        )
        assert approve.status_code == 200, approve.text

        post_flight = client.get(
            "/api/v1/professional-specifications",
            params={
                "project_id": str(nex_horizont.id),
                "approved_by": str(zoltan.id),
            },
        )
        assert post_flight.status_code == 200
        assert post_flight.json()["total"] == 1
        assert post_flight.json()["items"][0]["id"] == draft_professional_spec["id"]

        # The module is still untouched — gating works even when
        # toggled mid-session: no design_documents row leaked in
        # during the closed-gate window.
        module_docs_after = client.get(
            "/api/v1/design-documents",
            params={"module_id": str(dob_module.id)},
        )
        assert module_docs_after.status_code == 200
        assert module_docs_after.json()["total"] == 0

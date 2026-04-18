"""Integration test for BEHAVIOR.md §3.3 ``workflow:approve_professional_spec``.

Exercises the full happy path of the **approve_professional_spec**
workflow end-to-end through the real FastAPI ``app``. The workflow
takes a draft :class:`ProfessionalSpecification` (the postcondition of
workflow §3.2, verified in ``test_workflow_generate_professional_spec``)
and flips it to the approved state — stamping ``approved_by`` with the
``ri``-role user who clicked "Schváliť špecifikáciu" and
``approved_at`` with the current timestamp. The approved state is the
precondition for workflow §3.4 (``generate_design_md``) so the edge
case §4.2 (``edge:design_approval_without_spec_approval``) is the
gate this workflow opens.

    Precondition (per BEHAVIOR.md §3.3):
        * A :class:`ProfessionalSpecification` exists (``version=1``,
          ``approved_by=NULL`` — the draft state produced by workflow
          §3.2).
        * The actor has role ``ri`` (``ri_director`` Zoltán or
          ``ri_senior`` Tibor per BEHAVIOR.md §1.1).

    Steps (per BEHAVIOR.md §3.3):
        1. Zoltán opens the professional specification
           → ``GET /api/v1/professional-specifications/{id}`` returns
           the draft row for review.
        2. Zoltán (optionally) edits the content — e.g. adds the
           "Objednávka musí byť pre jedného dodávateľa" constraint
           from the worked example
           → ``PATCH /api/v1/professional-specifications/{id}`` with
           ``{"content": ...}``. The system persists the edit and
           leaves ``approved_by`` / ``approved_at`` untouched.
        3. Zoltán clicks "Schváliť špecifikáciu" and confirms
           → ``PATCH /api/v1/professional-specifications/{id}`` with
           ``{"approved_by": <zoltan_uuid>}``. The service stamps
           ``approved_at = now()`` automatically.

    Postcondition (per BEHAVIOR.md §3.3):
        * ``professional_specifications`` row has ``approved_by !=
          NULL`` and ``approved_at != NULL``.
        * The same row still carries the edited ``content`` from
          step 2 — approval does not overwrite use-case edits.
        * The edit from step 2 and the approval from step 3 are
          visible to a subsequent ``GET`` — the
          ``SpecificationViewer`` re-renders the approved spec with
          a grey-out state and an active "Generovať DESIGN.md"
          button (DESIGN.md §3.1, §9 / §10 pipeline gating).

At least one edge case is verified alongside the happy path:

    * **Approving a non-existent specification** — the UI would
      never allow this, but a direct API call with a stale /
      fabricated UUID must be rejected with HTTP 404 and must not
      create any side effect. This is the baseline error-handling
      contract exercised on every ``/{id}`` endpoint of the router
      (per ``_map_value_error``).
    * **Auto-stamp is a one-shot transition** — a subsequent PATCH
      after approval (e.g. a content fix) does **not** overwrite
      the original ``approved_at`` stamp, because the auto-stamp
      guard is keyed on ``spec.approved_by is None`` at entry.
      Approval is "sticky" — mirroring the service-layer docstring
      and the §10 pipeline-gating contract (the approved state is
      monotonic and cannot be silently re-stamped).

Auth note:
    The current codebase (Feats 0-6) wires routers directly without a
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
from backend.db.models.projects import Project
from backend.db.models.specifications import ProfessionalSpecification, RawSpecification

# ---------------------------------------------------------------------------
# Precondition fixtures — Zoltán (ri_director) and Tibor (ri_senior) as
# members of NEX Horizont, with a draft professional specification
# already generated (postcondition of workflow §3.2).
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
    """Persist Tibor — the ``ri_senior`` alternative approver (BEHAVIOR.md §1.1).

    BEHAVIOR.md §3.3 names both Zoltán (``ri_director``) and Tibor
    (``ri_senior``) as valid actors for the approval workflow. The
    fixture lets the happy-path variation cover Tibor approving in
    Zoltán's absence — same role (``ri``), same contract.
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

    Matches the BEHAVIOR.md §3.3 worked example: Zoltán approves the
    professional specification for the DOB module of NEX Horizont.
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

    return project


# Markdown bodies — the "before" is what workflow §3.2 produced for
# the ANDROS DOB email; the "after" mirrors the "Konkrétny príklad"
# of §3.3 ("doplní constraint 'Objednávka musí byť pre jedného
# dodávateľa'").
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
    "- Exportný súbor podľa špecifikácie účtovného systému.\n\n"
    "## Out of Scope\n"
    "- Platobné brány — nie je súčasťou modulu DOB.\n"
)

ANDROS_DOB_PROFESSIONAL_SPEC_EDITED = (
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
    # The edit Zoltán makes in BEHAVIOR.md §3.3's worked example:
    "- Objednávka musí byť pre jedného dodávateľa.\n"
    "- Exportný súbor podľa špecifikácie účtovného systému.\n\n"
    "## Out of Scope\n"
    "- Platobné brány — nie je súčasťou modulu DOB.\n"
)


@pytest.fixture()
def draft_professional_spec(client, db_session, nex_horizont, zoltan) -> dict[str, Any]:
    """Seed a draft professional specification (postcondition of §3.2).

    Mirrors workflow §3.2 Step 3: the AI has produced a structured
    markdown spec with ``version=1`` and ``approved_by=NULL``. The
    parent ``raw_specifications`` row is persisted directly on the
    ``db_session`` (its own lifecycle is covered in §3.1 / §3.2) and
    the professional specification itself is created via the real
    HTTP endpoint so the test exercises the full router path.
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

    resp = client.post(
        "/api/v1/professional-specifications",
        json={
            "raw_spec_id": str(raw.id),
            "project_id": str(nex_horizont.id),
            "content": ANDROS_DOB_PROFESSIONAL_SPEC,
        },
    )
    assert resp.status_code == 201, resp.text
    row = resp.json()
    # Sanity-check the precondition explicitly so a schema regression
    # surfaces here, not deep inside the approval-step assertions.
    assert row["approved_by"] is None
    assert row["approved_at"] is None
    assert row["version"] == 1
    return row


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.3 end-to-end.
# ---------------------------------------------------------------------------


class TestApproveProfessionalSpecHappyPath:
    """End-to-end walkthrough of workflow §3.3 against the real app."""

    def test_full_workflow_with_use_case_edit(self, client, db_session, zoltan, nex_horizont, draft_professional_spec):
        """Drive steps 1-4 of the workflow — view, edit, approve, confirm.

        The test asserts both the HTTP contract (status codes, payload
        shape) and the database state after each step. The worked
        example from BEHAVIOR.md §3.3 is reproduced verbatim: Zoltán
        adds the "Objednávka musí byť pre jedného dodávateľa"
        constraint before approving.
        """
        spec_id = draft_professional_spec["id"]

        # --- Step 1: Zoltán opens the professional specification.
        # The SpecificationViewer fetches the draft row for display.
        get_resp = client.get(f"/api/v1/professional-specifications/{spec_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["id"] == spec_id
        assert get_resp.json()["content"] == ANDROS_DOB_PROFESSIONAL_SPEC
        assert get_resp.json()["approved_by"] is None
        assert get_resp.json()["approved_at"] is None

        # --- Step 2: Zoltán edits a use case / constraint.
        # "Systém priebežne ukladá zmeny" — the edit is a plain PATCH
        # on ``content`` and leaves the approval columns untouched.
        before_approval = datetime.now(tz=timezone.utc)
        edit_resp = client.patch(
            f"/api/v1/professional-specifications/{spec_id}",
            json={"content": ANDROS_DOB_PROFESSIONAL_SPEC_EDITED},
        )
        assert edit_resp.status_code == 200, edit_resp.text
        edited = edit_resp.json()
        assert edited["content"] == ANDROS_DOB_PROFESSIONAL_SPEC_EDITED
        # Editing content must not silently approve the spec.
        assert edited["approved_by"] is None
        assert edited["approved_at"] is None
        # Version is not bumped by the router — regeneration would
        # create a new row instead. The edit is an in-place PATCH.
        assert edited["version"] == 1

        # --- Step 3: Zoltán clicks "Schváliť špecifikáciu". The UI
        # shows a confirmation dialog — modelled client-side; the
        # HTTP contract collapses steps 3 and 4 into a single PATCH.
        # --- Step 4: Zoltán confirms.
        # System stamps ``approved_by=zoltan_id``, ``approved_at=NOW()``.
        approve_resp = client.patch(
            f"/api/v1/professional-specifications/{spec_id}",
            json={"approved_by": str(zoltan.id)},
            # ``approved_at`` deliberately omitted — the service
            # auto-stamps it on transition from NULL → user_id.
        )
        assert approve_resp.status_code == 200, approve_resp.text
        approved = approve_resp.json()
        assert approved["approved_by"] == str(zoltan.id)
        assert approved["approved_at"] is not None
        # Edits from step 2 must survive the approval PATCH.
        assert approved["content"] == ANDROS_DOB_PROFESSIONAL_SPEC_EDITED

        # --- Postcondition: the ``SpecificationViewer`` re-renders
        # the approved spec on the next GET — the approval is
        # observable to the UI exactly as the user sees it.
        after_resp = client.get(f"/api/v1/professional-specifications/{spec_id}")
        assert after_resp.status_code == 200
        after = after_resp.json()
        assert after["approved_by"] == str(zoltan.id)
        assert after["approved_at"] == approved["approved_at"]

        # --- Postcondition verification (DB state) --------------------
        db_session.expire_all()
        persisted = db_session.get(ProfessionalSpecification, uuid.UUID(spec_id))
        assert persisted is not None
        # 1. ``approved_by != NULL`` and matches the approver's UUID.
        assert persisted.approved_by == zoltan.id
        # 2. ``approved_at != NULL`` and was stamped during this test
        #    (the service uses ``datetime.now(tz=timezone.utc)``).
        assert persisted.approved_at is not None
        assert persisted.approved_at >= before_approval
        assert persisted.approved_at <= datetime.now(tz=timezone.utc)
        # 3. The edit from step 2 is persisted — approval does not
        #    overwrite use-case edits.
        assert persisted.content == ANDROS_DOB_PROFESSIONAL_SPEC_EDITED
        # 4. Immutable attributes still hold (workflow §3.3 "Data
        #    touched" is only ``professional_specifications``, and
        #    only the approval columns + content are mutated).
        assert persisted.version == 1
        assert persisted.project_id == nex_horizont.id

        # --- Postcondition: the approved spec is discoverable via
        # the ``approved_by`` filter. This is the query the
        # ``SpecificationPage`` would run to list a user's approved
        # specifications (DESIGN.md §3.1).
        list_approved = client.get(
            "/api/v1/professional-specifications",
            params={"approved_by": str(zoltan.id)},
        )
        assert list_approved.status_code == 200
        approved_ids = [row["id"] for row in list_approved.json()["items"]]
        assert spec_id in approved_ids

    def test_approve_without_editing_use_cases(self, client, db_session, zoltan, nex_horizont, draft_professional_spec):
        """Step 2 is explicitly optional — approval works without any edit.

        BEHAVIOR.md §3.3 marks step 2 as "voliteľne" ("optionally").
        When Zoltán is happy with the AI-generated draft, he can
        skip the edit and go straight from step 1 (view) to step 3
        (approve). The postcondition is identical — approval is
        the side effect that matters, not the edit.
        """
        spec_id = draft_professional_spec["id"]
        before_approval = datetime.now(tz=timezone.utc)

        approve_resp = client.patch(
            f"/api/v1/professional-specifications/{spec_id}",
            json={"approved_by": str(zoltan.id)},
        )
        assert approve_resp.status_code == 200, approve_resp.text
        approved = approve_resp.json()
        assert approved["approved_by"] == str(zoltan.id)
        assert approved["approved_at"] is not None
        # The content survives unchanged — no accidental edit.
        assert approved["content"] == ANDROS_DOB_PROFESSIONAL_SPEC

        db_session.expire_all()
        persisted = db_session.get(ProfessionalSpecification, uuid.UUID(spec_id))
        assert persisted is not None
        assert persisted.approved_by == zoltan.id
        assert persisted.approved_at is not None
        assert persisted.approved_at >= before_approval
        assert persisted.content == ANDROS_DOB_PROFESSIONAL_SPEC

    def test_ri_senior_may_also_approve(self, client, db_session, tibor, nex_horizont, draft_professional_spec):
        """``ri_senior`` (Tibor) is an equally valid approver per §3.3.

        BEHAVIOR.md §3.3 lists the actor as "[[actor:ri_director]]
        alebo [[actor:ri_senior]]" — both roles resolve to ``role='ri'``
        at the DB level. The router accepts any user with that role
        as ``approved_by``; enforcement is identical regardless of
        which specific ``ri`` user does the click.
        """
        spec_id = draft_professional_spec["id"]

        resp = client.patch(
            f"/api/v1/professional-specifications/{spec_id}",
            json={"approved_by": str(tibor.id)},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["approved_by"] == str(tibor.id)
        assert resp.json()["approved_at"] is not None

        db_session.expire_all()
        persisted = db_session.get(ProfessionalSpecification, uuid.UUID(spec_id))
        assert persisted is not None
        assert persisted.approved_by == tibor.id


# ---------------------------------------------------------------------------
# Edge cases.
# ---------------------------------------------------------------------------


class TestApproveProfessionalSpecEdgeCases:
    """Edge cases for the ``approve_professional_spec`` workflow.

    The UI gates every approval via the ``SpecificationViewer`` —
    these tests cover direct-API misuse and the "approval is
    monotonic" contract that BEHAVIOR.md §3.3 / §10 pipeline gating
    implies but does not spell out as a separate §4 edge.
    """

    def test_approving_nonexistent_spec_returns_404(self, client, db_session, zoltan):
        """PATCH on a fabricated UUID → 404, no DB side effect.

        A stale browser tab or a direct API hit with a wrong UUID
        must not corrupt the database. The router's
        ``_map_value_error`` translates the service-layer
        ``ValueError("... not found")`` into an HTTP 404 and
        rolls back the session (no row is created / touched).
        """
        fabricated = uuid.uuid4()

        resp = client.patch(
            f"/api/v1/professional-specifications/{fabricated}",
            json={"approved_by": str(zoltan.id)},
        )
        assert resp.status_code == 404, resp.text
        assert "not found" in resp.json()["detail"].lower()

        # No row was created as a side effect of the failed approval.
        db_session.expire_all()
        assert db_session.get(ProfessionalSpecification, fabricated) is None

    def test_approval_is_sticky_on_subsequent_patch(
        self, client, db_session, zoltan, nex_horizont, draft_professional_spec
    ):
        """A post-approval content fix does NOT re-stamp ``approved_at``.

        Once a spec has ``approved_by != NULL``, subsequent PATCHes
        (e.g. a typo fix in a use case) must not overwrite the
        original ``approved_at`` — the auto-stamp guard is keyed on
        ``spec.approved_by is None`` at entry, so a second
        transition can't silently update the stamp. This is the
        observable side of the §10 pipeline-gating contract: the
        approved state is monotonic, and the stamp marks when the
        ``ri`` user originally clicked "Schváliť".

        (An explicit un-approval flow is not modelled by this
        service — it belongs to admin tooling.)
        """
        spec_id = draft_professional_spec["id"]

        # --- First approval — stamps ``approved_at`` automatically.
        first = client.patch(
            f"/api/v1/professional-specifications/{spec_id}",
            json={"approved_by": str(zoltan.id)},
        )
        assert first.status_code == 200, first.text
        original_approved_at = first.json()["approved_at"]
        assert original_approved_at is not None

        # --- Later edit — Zoltán fixes a typo in a use case. The
        # router is happy to accept the PATCH, but the approval
        # stamp must not slide forward.
        second = client.patch(
            f"/api/v1/professional-specifications/{spec_id}",
            json={"content": ANDROS_DOB_PROFESSIONAL_SPEC_EDITED},
        )
        assert second.status_code == 200, second.text
        assert second.json()["content"] == ANDROS_DOB_PROFESSIONAL_SPEC_EDITED
        # The approval columns are unchanged.
        assert second.json()["approved_by"] == str(zoltan.id)
        assert second.json()["approved_at"] == original_approved_at

        db_session.expire_all()
        persisted = db_session.get(ProfessionalSpecification, uuid.UUID(spec_id))
        assert persisted is not None
        assert persisted.approved_by == zoltan.id
        # Compare via parsed ``datetime`` — the JSON encoder emits
        # trailing ``Z`` while ``datetime.isoformat()`` emits
        # ``+00:00``; both denote the same UTC instant.
        assert persisted.approved_at is not None
        assert persisted.approved_at == datetime.fromisoformat(original_approved_at.replace("Z", "+00:00"))

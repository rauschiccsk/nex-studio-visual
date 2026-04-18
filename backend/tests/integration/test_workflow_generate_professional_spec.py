"""Integration test for BEHAVIOR.md §3.2 ``workflow:generate_professional_spec``.

Exercises the full happy path of the **generate_professional_spec**
workflow end-to-end through the real FastAPI ``app``. The AI /
Anthropic API call itself is out of scope for this layer — the test
treats the AI as a black box and verifies the *observable* side
effects of the workflow: the ``raw_specifications`` lifecycle
(``pending`` → ``processing`` → ``done``) and the creation of a
fresh ``professional_specifications`` row attached to the same raw
spec and project.

    Precondition (per BEHAVIOR.md §3.2):
        * A :class:`RawSpecification` exists for the NEX Horizont
          project with ``status='pending'`` (the postcondition of
          workflow §3.1, verified in
          ``test_workflow_create_raw_specification.py``).
        * Claude API is considered available — the happy-path test
          models a successful generation; the "API failure" branch is
          covered by the edge-case class below.

    Steps (per BEHAVIOR.md §3.2):
        1. System flips ``raw_spec.status='processing'`` (entry to the
           workflow — the same PATCH that ends workflow §3.1).
        2. AI generates the structured professional specification
           (black-boxed — the test provides the same markdown the AI
           would have produced for the ANDROS DOB email).
        3. System persists :class:`ProfessionalSpecification` with
           ``version=1`` and ``approved_by=NULL``
           → ``POST /api/v1/professional-specifications``.
        4. System flips ``raw_spec.status='done'``
           → ``PATCH /api/v1/raw-specifications/{id}``.
        5. Zoltán sees a "ready for review" notification — modelled as
           ``GET /api/v1/professional-specifications?raw_spec_id=...``
           returning the newly created row so the
           ``SpecificationViewer`` can render it.

    Postcondition (per BEHAVIOR.md §3.2):
        * ``professional_specifications`` row exists with
          ``version=1``, ``approved_by=NULL`` and ``approved_at=NULL``
          (editable, ready for Zoltán's review).
        * ``raw_specifications`` row has ``status='done'``.
        * The professional spec references the *original* raw spec
          (``raw_spec_id``) and project (``project_id``).

At least one edge case is verified alongside the happy path:

    * **Claude API failure** (BEHAVIOR.md §4.1
      ``edge:spec_generation_claude_api_failure``) — the AI call
      returns an error. The system marks
      ``raw_spec.status='failed'`` and creates **no** partial
      professional specification (per the spec: "Systém NEUROBÍ
      čiastočný záznam professional spec"). The list endpoint for
      the raw spec therefore returns zero rows.

Auth note:
    The current codebase (Feats 0–6) wires routers directly without a
    JWT dependency, so the integration test does not exercise a login
    flow. The "AI generates for ri-submitted raw spec" precondition is
    satisfied by persisting Zoltán with ``role='ri'`` and using his
    UUID as ``created_by`` on the raw spec. Role enforcement at the
    router level is a separate concern covered by future
    auth-middleware tests.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.specifications import ProfessionalSpecification, RawSpecification

# ---------------------------------------------------------------------------
# Precondition fixtures — Zoltán (ri) as a member of NEX Horizont with a
# raw spec already uploaded (postcondition of workflow §3.1).
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
def nex_horizont(db_session, zoltan) -> Project:
    """Persist the NEX Horizont project and add Zoltán as a member.

    Matches the BEHAVIOR.md §3.2 worked example: the AI transforms the
    raw ANDROS s.r.o. email that Zoltán uploaded for the NEX Horizont
    project into a structured professional specification.
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


# ANDROS s.r.o. customer email (Slovak, abbreviated) — mirrors the
# "Konkrétny príklad" in BEHAVIOR.md §3.1 / §3.2.
ANDROS_DOB_EMAIL = (
    "Dobrý deň,\n\n"
    "v rámci digitalizácie nákupu potrebujeme modul Dodávateľské "
    "objednávky (DOB) s nasledujúcimi funkciami:\n"
    "- evidencia dodávateľov a ich zmluvných podmienok,\n"
    "- schvaľovanie objednávok cez viacstupňový workflow,\n"
    "- napojenie na účtovníctvo cez exportný súbor.\n\n"
    "S pozdravom,\nANDROS s.r.o."
)

# Mirrors the AI-generated "Konkrétny príklad" in BEHAVIOR.md §3.2 —
# what Claude would return for the ANDROS DOB email.
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


@pytest.fixture()
def pending_raw_spec(client, nex_horizont, zoltan) -> dict[str, Any]:
    """Create a ``pending`` raw specification via the real HTTP endpoint.

    Seeds the workflow's precondition exactly the same way workflow
    §3.1 would in production: ``POST /api/v1/raw-specifications`` with
    the ANDROS DOB email. The returned JSON row (with
    ``status='pending'``) is the input to workflow §3.2.
    """
    resp = client.post(
        "/api/v1/raw-specifications",
        json={
            "project_id": str(nex_horizont.id),
            "created_by": str(zoltan.id),
            "input_text": ANDROS_DOB_EMAIL,
            "input_format": "text",
        },
    )
    assert resp.status_code == 201, resp.text
    row = resp.json()
    assert row["status"] == "pending"
    return row


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.2 end-to-end.
# ---------------------------------------------------------------------------


class TestGenerateProfessionalSpecHappyPath:
    """End-to-end walkthrough of workflow §3.2 against the real app."""

    def test_full_workflow(self, client, db_session, zoltan, nex_horizont, pending_raw_spec):
        """Drive steps 1-5 of the workflow and verify the postcondition.

        The test asserts both the HTTP contract (status codes, payload
        shape) and the database state after each step. The Claude API
        call itself is black-boxed — the test provides the markdown the
        AI would have produced for the ANDROS DOB email.
        """
        raw_spec_id = pending_raw_spec["id"]

        # --- Precondition: raw spec exists with status='pending'. No
        # professional specification has been generated yet.
        initial_list = client.get(
            "/api/v1/professional-specifications",
            params={"raw_spec_id": raw_spec_id},
        )
        assert initial_list.status_code == 200, initial_list.text
        assert initial_list.json()["total"] == 0
        assert initial_list.json()["items"] == []

        # --- Step 1: system flips raw_spec.status='processing'.
        # Entry to workflow §3.2 — identical to the final step of
        # workflow §3.1.
        patch_to_processing = client.patch(
            f"/api/v1/raw-specifications/{raw_spec_id}",
            json={"status": "processing"},
        )
        assert patch_to_processing.status_code == 200, patch_to_processing.text
        assert patch_to_processing.json()["status"] == "processing"

        # --- Step 2: AI generates the professional specification.
        # Black-boxed — the test supplies the output the AI would have
        # produced from the ANDROS DOB email (see "Konkrétny príklad"
        # in BEHAVIOR.md §3.2).

        # --- Step 3: system persists professional_specifications with
        # version=1 and approved_by=NULL (editable, pending review).
        create_resp = client.post(
            "/api/v1/professional-specifications",
            json={
                "raw_spec_id": raw_spec_id,
                "project_id": str(nex_horizont.id),
                "content": ANDROS_DOB_PROFESSIONAL_SPEC,
                # ``version`` is omitted on purpose — the DB / schema
                # default of 1 is the correct value for the initial
                # generation (BEHAVIOR.md §3.2 Step 3).
                # ``approved_by`` / ``approved_at`` are also omitted —
                # the document is pending Zoltán's review.
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        created = create_resp.json()
        assert created["raw_spec_id"] == raw_spec_id
        assert created["project_id"] == str(nex_horizont.id)
        assert created["content"] == ANDROS_DOB_PROFESSIONAL_SPEC
        assert created["version"] == 1  # Step 3 postcondition.
        assert created["approved_by"] is None  # Step 3 postcondition.
        assert created["approved_at"] is None
        assert created["id"]
        assert created["created_at"]
        assert created["updated_at"]

        # --- Step 4: system flips raw_spec.status='done'.
        patch_to_done = client.patch(
            f"/api/v1/raw-specifications/{raw_spec_id}",
            json={"status": "done"},
        )
        assert patch_to_done.status_code == 200, patch_to_done.text
        assert patch_to_done.json()["status"] == "done"

        # --- Step 5: Zoltán (or any project member) can list the
        # newly generated spec — this is what the SpecificationViewer
        # notification drives the user to.
        list_after = client.get(
            "/api/v1/professional-specifications",
            params={"raw_spec_id": raw_spec_id},
        )
        assert list_after.status_code == 200
        assert list_after.json()["total"] == 1
        assert [row["id"] for row in list_after.json()["items"]] == [created["id"]]

        # --- Postcondition verification (DB state) --------------------
        db_session.expire_all()

        # 1. professional_specifications row exists, version=1, not yet
        #    approved. The editable "draft" state per BEHAVIOR.md §3.2.
        persisted_prof = db_session.get(ProfessionalSpecification, uuid.UUID(created["id"]))
        assert persisted_prof is not None
        assert persisted_prof.version == 1
        assert persisted_prof.approved_by is None
        assert persisted_prof.approved_at is None
        assert persisted_prof.raw_spec_id == uuid.UUID(raw_spec_id)
        assert persisted_prof.project_id == nex_horizont.id
        assert persisted_prof.content == ANDROS_DOB_PROFESSIONAL_SPEC

        # 2. raw_specifications row is now status='done'.
        persisted_raw = db_session.get(RawSpecification, uuid.UUID(raw_spec_id))
        assert persisted_raw is not None
        assert persisted_raw.status == "done"
        # The original uploader / input text / project attribution
        # must still hold — §3.2 doesn't touch those columns.
        assert persisted_raw.created_by == zoltan.id
        assert persisted_raw.project_id == nex_horizont.id
        assert persisted_raw.input_text == ANDROS_DOB_EMAIL

    def test_default_version_is_one_when_omitted(self, client, db_session, zoltan, nex_horizont, pending_raw_spec):
        """Explicitly verify the ``version=1`` default from BEHAVIOR.md §3.2.

        The workflow specifies *"Systém vytvorí professional_specifications
        s version=1"*. This test pins that default against the Pydantic
        schema / DB ``server_default`` so a silent change to either is
        caught immediately.
        """
        raw_spec_id = pending_raw_spec["id"]

        resp = client.post(
            "/api/v1/professional-specifications",
            json={
                "raw_spec_id": raw_spec_id,
                "project_id": str(nex_horizont.id),
                "content": ANDROS_DOB_PROFESSIONAL_SPEC,
                # ``version`` deliberately omitted — must default to 1.
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["version"] == 1

        db_session.expire_all()
        persisted = db_session.get(ProfessionalSpecification, uuid.UUID(resp.json()["id"]))
        assert persisted is not None
        assert persisted.version == 1


# ---------------------------------------------------------------------------
# Edge case — Claude API failure (BEHAVIOR.md §4.1).
# ---------------------------------------------------------------------------


class TestGenerateProfessionalSpecEdgeCases:
    """Edge cases for the ``generate_professional_spec`` workflow.

    The AI-generation workflow is brittle by nature — the Claude API
    can time out, return 5xx, or be unavailable. BEHAVIOR.md §4.1
    specifies the contract: flip the raw spec to ``failed`` and create
    **no** partial professional specification row.
    """

    def test_claude_api_failure_leaves_no_partial_professional_spec(
        self, client, db_session, zoltan, nex_horizont, pending_raw_spec
    ):
        """AI generation fails → ``raw_spec.status='failed'``, no prof spec.

        Models BEHAVIOR.md §4.1 ``edge:spec_generation_claude_api_failure``:

        * Step 1 still runs — the system flips
          ``raw_spec.status='processing'`` before calling the AI.
        * The AI call fails (timeout / 5xx) — the caller *skips* the
          professional-spec POST and instead patches the raw spec to
          ``status='failed'``.
        * "Systém NEUROBÍ čiastočný záznam professional spec" — the
          ``professional_specifications`` list for this raw spec stays
          empty.
        """
        raw_spec_id = pending_raw_spec["id"]

        # Step 1: system starts the workflow — raw_spec='processing'.
        start = client.patch(
            f"/api/v1/raw-specifications/{raw_spec_id}",
            json={"status": "processing"},
        )
        assert start.status_code == 200

        # Step 2 (simulated): Claude API returns an error. The orchestrator
        # does NOT call POST /professional-specifications — it instead
        # marks the raw spec as failed and surfaces the error to the UI.
        fail = client.patch(
            f"/api/v1/raw-specifications/{raw_spec_id}",
            json={"status": "failed"},
        )
        assert fail.status_code == 200, fail.text
        assert fail.json()["status"] == "failed"

        # --- Postcondition: no partial professional specification exists.
        list_resp = client.get(
            "/api/v1/professional-specifications",
            params={"raw_spec_id": raw_spec_id},
        )
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] == 0
        assert list_resp.json()["items"] == []

        # --- Postcondition (DB): raw spec is 'failed', no prof spec rows
        # reference this raw_spec_id regardless of project filter.
        db_session.expire_all()
        persisted_raw = db_session.get(RawSpecification, uuid.UUID(raw_spec_id))
        assert persisted_raw is not None
        assert persisted_raw.status == "failed"

        all_project_specs = client.get(
            "/api/v1/professional-specifications",
            params={"project_id": str(nex_horizont.id)},
        )
        assert all_project_specs.status_code == 200
        assert all_project_specs.json()["total"] == 0

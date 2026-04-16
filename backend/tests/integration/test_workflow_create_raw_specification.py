"""Integration test for BEHAVIOR.md §3.1 ``workflow:create_raw_specification``.

Exercises the full happy path of the **create_raw_specification**
workflow end-to-end through the real FastAPI ``app``:

    Precondition:
        * Zoltán (user with role ``ri``) is persisted.
        * A project exists and Zoltán is its member (via
          ``project_members``).
        * The project has no outstanding raw specification in
          ``status='processing'`` (no such row exists yet).

    Steps (per BEHAVIOR.md §3.1):
        1. Zoltán opens the project → "Specifications" tab
           → the ``GET /api/v1/raw-specifications?project_id=...`` list
           returns an empty envelope.
        2. Zoltán clicks "New specification", picks format ``text``.
        3. Zoltán pastes the customer specification text (email from
           ANDROS s.r.o. with requirements for the DOB module — see the
           "Konkrétny príklad" block of the workflow).
        4. Zoltán clicks "Send for processing"
           → ``POST /api/v1/raw-specifications`` returns HTTP 201 and a
           row with ``status='pending'`` and ``created_by=zoltan.id``.
        5. The system flips the status to ``processing`` (entry to the
           ``generate_professional_spec`` workflow) — modelled here as
           an explicit ``PATCH /api/v1/raw-specifications/{id}`` with
           ``status='processing'``.

    Postcondition (per BEHAVIOR.md §3.1):
        * The ``raw_specifications`` row exists with ``status`` in
          {``pending``, ``processing``}.
        * ``created_by`` is the uploader (Zoltán).
        * The list endpoint returns the newly created row.

At least one edge case is verified alongside the happy path:

    * **Empty customer specification text** — the customer pasted a
      blank payload. The ``POST`` must reject it with HTTP 422 (guarded
      by ``RawSpecificationCreate.input_text.min_length=1``). Nothing is
      written to the database (list total stays at 0).

Auth note:
    The current codebase (Feats 0–6) wires routers directly without a
    JWT dependency, so the integration test does not exercise a login
    flow. The "role=ri" precondition is satisfied by persisting the
    user with ``role='ri'`` and passing ``created_by=zoltan.id`` on the
    payload. Role enforcement at the router level is a separate
    concern covered by future auth-middleware tests.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project, ProjectMember
from backend.db.models.specifications import RawSpecification

# ---------------------------------------------------------------------------
# Precondition fixtures — Zoltán (ri) as a member of NEX Horizont.
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

    Matches the BEHAVIOR.md §3.1 worked example: Zoltán uploads a raw
    specification for the NEX Horizont project.
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


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


# ANDROS s.r.o. customer email (Slovak, abbreviated) — mirrors the
# "Konkrétny príklad" in BEHAVIOR.md §3.1.
ANDROS_DOB_EMAIL = (
    "Dobrý deň,\n\n"
    "v rámci digitalizácie nákupu potrebujeme modul Dodávateľské "
    "objednávky (DOB) s nasledujúcimi funkciami:\n"
    "- evidencia dodávateľov a ich zmluvných podmienok,\n"
    "- schvaľovanie objednávok cez viacstupňový workflow,\n"
    "- napojenie na účtovníctvo cez exportný súbor.\n\n"
    "S pozdravom,\nANDROS s.r.o."
)


def _create_payload(project_id: uuid.UUID, user_id: uuid.UUID, **overrides: Any) -> dict:
    """Build a JSON payload for ``POST /api/v1/raw-specifications``.

    Defaults match Step 2 of the workflow (format ``text``) and Step 3
    (ANDROS DOB email as ``input_text``). Overrides let individual tests
    swap fields (e.g. an empty ``input_text`` for the edge case).
    """
    payload: dict = {
        "project_id": str(project_id),
        "created_by": str(user_id),
        "input_text": ANDROS_DOB_EMAIL,
        "input_format": "text",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.1 end-to-end.
# ---------------------------------------------------------------------------


class TestCreateRawSpecificationHappyPath:
    """End-to-end walkthrough of workflow §3.1 against the real app."""

    def test_full_workflow(self, client, db_session, zoltan, nex_horizont):
        """Drive steps 1-5 of the workflow and verify the postcondition.

        The test asserts both the HTTP contract (status codes, payload
        shape) and the database state after each step.
        """
        # --- Precondition: project has no outstanding raw spec ----------
        initial_list = client.get(
            "/api/v1/raw-specifications",
            params={"project_id": str(nex_horizont.id)},
        )
        assert initial_list.status_code == 200, initial_list.text
        assert initial_list.json()["total"] == 0
        assert initial_list.json()["items"] == []

        # --- Steps 2-4: create the raw specification --------------------
        create_resp = client.post(
            "/api/v1/raw-specifications",
            json=_create_payload(nex_horizont.id, zoltan.id),
        )
        assert create_resp.status_code == 201, create_resp.text
        created = create_resp.json()
        assert created["project_id"] == str(nex_horizont.id)
        assert created["created_by"] == str(zoltan.id)
        assert created["input_format"] == "text"
        assert created["language"] == "sk"  # Slovak email — DB default.
        assert created["status"] == "pending"  # Step 4 postcondition.
        assert created["input_text"] == ANDROS_DOB_EMAIL
        assert created["id"]
        assert created["created_at"]
        assert created["updated_at"]

        # The new row is visible on the list (Step 1 for a subsequent
        # visitor: "Systém zobrazí zoznam existujúcich špecifikácií").
        after_create_list = client.get(
            "/api/v1/raw-specifications",
            params={"project_id": str(nex_horizont.id)},
        )
        assert after_create_list.status_code == 200
        assert after_create_list.json()["total"] == 1
        assert [row["id"] for row in after_create_list.json()["items"]] == [created["id"]]

        # --- Step 5: system flips status to ``processing`` --------------
        # In production this happens inside ``generate_professional_spec``;
        # here we verify the lifecycle transition is a plain PATCH.
        patch_resp = client.patch(
            f"/api/v1/raw-specifications/{created['id']}",
            json={"status": "processing"},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        assert patch_resp.json()["status"] == "processing"

        # --- Postcondition verification --------------------------------
        # 1. The row exists with ``status`` in {pending, processing}.
        db_session.expire_all()
        persisted = db_session.get(RawSpecification, uuid.UUID(created["id"]))
        assert persisted is not None
        assert persisted.status in {"pending", "processing"}
        # 2. The uploader (Zoltán) is recorded — "Audit log obsahuje
        #    created_by=zoltan".
        assert persisted.created_by == zoltan.id
        # 3. The row is attributed to the right project.
        assert persisted.project_id == nex_horizont.id
        # 4. The input text is preserved verbatim.
        assert persisted.input_text == ANDROS_DOB_EMAIL


# ---------------------------------------------------------------------------
# Edge case — empty customer specification.
# ---------------------------------------------------------------------------


class TestCreateRawSpecificationEdgeCases:
    """Edge cases around the workflow's Step 3 payload.

    BEHAVIOR.md §3.1 assumes a non-empty customer text; an empty paste
    is a user input error that must be rejected before any DB write.
    """

    def test_empty_input_text_is_rejected_and_no_row_is_created(self, client, db_session, zoltan, nex_horizont):
        """POST with empty ``input_text`` → 422 and no DB side effect."""
        resp = client.post(
            "/api/v1/raw-specifications",
            json=_create_payload(nex_horizont.id, zoltan.id, input_text=""),
        )
        assert resp.status_code == 422, resp.text

        # Nothing was persisted — the project remains empty.
        db_session.expire_all()
        list_resp = client.get(
            "/api/v1/raw-specifications",
            params={"project_id": str(nex_horizont.id)},
        )
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] == 0

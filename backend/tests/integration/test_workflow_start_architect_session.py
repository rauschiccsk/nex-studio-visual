"""Integration test for BEHAVIOR.md §3.9 ``workflow:start_architect_session``.

Exercises the full happy path of the **start_architect_session**
workflow end-to-end through the real FastAPI ``app``. The workflow is
Tibor (``ri_senior`` per BEHAVIOR.md §1.1) — or any ``ri`` / ``ha``
actor listed in §3.9 — opening an Architect chat session scoped to a
specific module of a project. The worked example (§3.9 "Konkrétny
príklad") opens an Architect for the ``STK`` module of NEX Horizont
once its DESIGN.md is approved and the prerequisite GSC module is
``done``. The assistant answers with a streamed reply; after the stream
completes the system persists the user turn and the assistant turn into
``architect_messages`` along with the final token counts and USD cost.

    Precondition (per BEHAVIOR.md §3.9):
        * The project exists and the actor is a member.
        * The module's DESIGN.md exists and is approved (for the
          module-level session path). Approval is represented on the
          ``design_documents`` row by ``approved_by IS NOT NULL`` —
          the same monotonic contract §3.5 pins.
        * No other active session exists for the same module
          (``architect_sessions.status='active'`` ∧ ``module_id=<stk>``
          is empty). This is a UI-level invariant: the HTTP surface
          exposes the filter through
          ``GET /api/v1/architect-sessions?module_id=...&status=active``
          — the UI refuses to show "Otvoriť Architect" until the
          query returns ``total=0``. The service itself does not
          enforce uniqueness, so the test observes the gate via the
          filter rather than asserting a server-side 409.

    Steps (per BEHAVIOR.md §3.9):
        1. Tibor opens NEX Horizont → module STK → clicks "Otvoriť
           Architect". The UI checks two preconditions:
                * STK's DESIGN.md is approved — modelled as
                  ``GET /api/v1/design-documents?project_id=...&module_id=<stk>&doc_type=design``
                  with ``approved_by`` populated on the newest row.
                * No active Architect session exists for STK —
                  modelled as
                  ``GET /api/v1/architect-sessions?module_id=<stk>&status=active``
                  returning ``total=0``.
        2. — (system) — the system assembles the context for the
           Architect: Foundation DESIGN.md + module DESIGN.md +
           module registry + ICC-wide KB. The test models the
           observable HTTP side of this step: a Foundation
           DESIGN.md (``module_id IS NULL``, ``doc_type='design'``)
           exists, the STK DESIGN.md exists, and the registry query
           returns both modules along with their statuses (so the
           Architect prompt can note that GSC is ``done``). Qdrant /
           KB retrieval is orchestration territory and out of scope
           for the DB-level integration test.
        3. — (system) — the system creates the Architect session
           with ``status='active'``:
           ``POST /api/v1/architect-sessions``.
        4. Tibor writes "Vygeneruj implementačný plán pre STK modul"
           → the orchestrator persists the user turn:
           ``POST /api/v1/architect-messages`` with ``role='user'``.
           The streamed assistant reply is Anthropic-streaming
           territory; the test jumps to step 5 immediately.
        5. — (system) — after the SSE stream completes the
           orchestrator persists the assistant turn with the final
           token counts and cost:
           ``POST /api/v1/architect-messages`` with ``role='assistant'``,
           ``input_tokens``, ``output_tokens`` and ``cost_usd``.

    Postcondition (per BEHAVIOR.md §3.9):
        * ``architect_sessions`` row exists with
          ``status='active'`` and the expected ``project_id`` /
          ``module_id`` / ``created_by``.
        * The two-turn conversation is persisted in
          ``architect_messages`` in order (``user`` then
          ``assistant``).
        * Token usage and USD cost are recorded on the assistant
          turn (the user turn carries no ``cost_usd`` — only the
          model reply has a measurable cost).

Edge cases verified alongside the happy path:

    * **Invalid status literal** — a ``POST`` with
      ``status='open'`` (or any other string outside the ``active |
      closed`` literal) is rejected at the Pydantic layer (HTTP
      422) by :class:`ArchitectSessionStatus`'s ``Literal`` type.
      The service never runs; no session is persisted. This mirrors
      the ``ck_architect_sessions_status`` DB CHECK one layer
      shallower.
    * **Non-existent session id on GET** — a ``GET`` against a
      random UUID returns HTTP 404 via the service-layer
      ``ValueError("ArchitectSession {id} not found")`` translated
      by :func:`_map_value_error` in
      :mod:`backend.api.routes.architect_sessions`.
    * **Session lifecycle — PATCH status to ``closed``** — a
      subsequent ``PATCH`` with ``{"status": "closed"}`` stamps
      ``closed_at`` automatically (the ``active → closed`` auto-close
      convenience in :mod:`backend.services.architect_session`) and
      frees the "one Architect per module" slot — the filter
      ``module_id=<stk>&status=active`` drops back to ``total=0``,
      so the UI re-enables "Otvoriť Architect". This pins the
      precondition gate closes cleanly for the next invocation of
      §3.9.
    * **Actor role equivalence** — BEHAVIOR.md §3.9 lists three
      valid actors: ``ri_director``, ``ri_senior`` and
      ``ha_medior``. The happy-path worked example is Tibor
      (``ri_senior``); companion tests pin that Zoltán
      (``ri_director``, ``role='ri'``) and Peter (``ha_medior``,
      ``role='ha'``) may also open sessions with identical router
      behaviour. The three are tested against separate modules /
      projects so the "one active session per module" invariant is
      respected.
    * **Project-level (Foundation) session** — ``module_id=None``
      denotes a Foundation / project-level Architect session
      (DESIGN.md §1.5 "NULL = Foundation/project session"). The
      §3.9 worked example is module-level, but the HTTP surface
      accepts the project-level variant identically — pinned so
      that future refactors do not accidentally make ``module_id``
      non-optional.

Auth note:
    The current codebase (Feats 0–6) wires routers directly without a
    JWT dependency, so the integration test does not exercise a login
    flow. The "role=ri/ha, member of project" precondition is
    satisfied by persisting the actor with the correct ``role`` and
    adding them to ``project_members``. Role enforcement at the router
    level is a separate concern covered by future auth-middleware
    tests.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from backend.db.models.architect import ArchitectMessage, ArchitectSession
from backend.db.models.foundation import User
from backend.db.models.projects import (
    ModuleDependency,
    Project,
    ProjectMember,
    ProjectModule,
)
from backend.db.models.specifications import DesignDocument

# ---------------------------------------------------------------------------
# Precondition fixtures — Zoltán (ri_director), Tibor (ri_senior), Peter
# (ha_medior); the NEX Horizont project with all three as members; a
# prerequisite GSC module in ``done`` (so the STK dependency is satisfied
# as in the §3.9 "Konkrétny príklad"); a STK module in ``in_design`` with
# an approved DESIGN.md; and a Foundation DESIGN.md so the Architect
# context-assembly step has both layers available.
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
    """Persist Tibor — the ``ri_senior`` primary actor in §3.9's worked example.

    BEHAVIOR.md §3.9 names Tibor as the actor throughout the steps
    table ("Tibor otvorí projekt NEX Horizont → modul STK"). Zoltán
    (``ri_director``) and Peter (``ha_medior``) are equally valid
    callers — the happy-path test follows the worked example with
    Tibor; the companion tests pin the two alternates.
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
def peter(db_session) -> User:
    """Persist Peter — the ``ha_medior`` actor from BEHAVIOR.md §1.1.

    BEHAVIOR.md §3.9 lists ``ha_medior`` as a valid actor alongside
    the two ``ri`` roles. ``ha_medior`` maps to ``role='ha'`` at the
    DB level.
    """
    user = User(
        username="peter",
        email="peter@isnex.ai",
        password_hash="hashed-placeholder",
        role="ha",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def nex_horizont(db_session, zoltan, tibor, peter) -> Project:
    """Persist the NEX Horizont project with all three actors as members.

    BEHAVIOR.md §3.9 precondition line 1 requires the actor to be a
    project member. Adding all three up-front lets the happy-path
    test (Tibor) and the companion equivalence tests (Zoltán, Peter)
    reuse the same project row without refixturing.
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
    db_session.add(ProjectMember(project_id=project.id, user_id=tibor.id))
    db_session.add(ProjectMember(project_id=project.id, user_id=peter.id))
    db_session.flush()
    return project


@pytest.fixture()
def gsc_done(db_session, nex_horizont) -> ProjectModule:
    """Persist the prerequisite GSC module **already in ``done``**.

    BEHAVIOR.md §3.9 "Konkrétny príklad" notes that the Architect
    context includes "informáciu že GSC je ``done`` (závislosť
    splnená)". Seeding GSC directly in ``done`` keeps the happy-path
    test focused on the STK session flow rather than on the
    transitive GSC lifecycle (that is already pinned by
    ``test_workflow_set_module_status``).
    """
    module = ProjectModule(
        project_id=nex_horizont.id,
        code="GSC",
        name="Globálne skladové karty",
        category="Sklad",
        status="done",
    )
    db_session.add(module)
    db_session.flush()
    return module


@pytest.fixture()
def stk_in_design(db_session, nex_horizont, gsc_done) -> ProjectModule:
    """Persist STK in ``in_design`` with a dependency edge on GSC.

    The §3.9 worked example opens the Architect for STK while it
    sits in ``in_design`` — the state between the approved DESIGN.md
    (§3.5 postcondition with dependencies not all ``done``) and the
    ``in_development`` hop. The edge on GSC is what lets the
    Architect report "GSC je ``done`` (závislosť splnená)".
    """
    module = ProjectModule(
        project_id=nex_horizont.id,
        code="STK",
        name="Skladové karty zásob",
        category="Sklad",
        status="in_design",
    )
    db_session.add(module)
    db_session.flush()

    db_session.add(
        ModuleDependency(
            module_id=module.id,
            depends_on_module_id=gsc_done.id,
        )
    )
    db_session.flush()
    return module


# The Foundation DESIGN.md for NEX Horizont. Trimmed but recognisable:
# lists the project-wide ``users`` and ``projects`` tables the §3.9
# "Konkrétny príklad" cites as part of the Architect context.
FOUNDATION_DESIGN_MD = (
    "# DESIGN.md — NEX Horizont (Foundation)\n\n"
    "## 1. Data model\n"
    "### 1.1 `users`\n"
    "- JWT-authenticated team members with `role IN ('ri','ha','shu')`.\n"
    "### 1.2 `projects`\n"
    "- Multimodule ERP successor.\n"
)


# The STK DESIGN.md. Lists the tables the §3.9 worked example calls out
# (``stock_items``, ``warehouses``).
STK_DESIGN_MD = (
    "# DESIGN.md — modul STK (Skladové karty zásob)\n\n"
    "## 1. Data model\n"
    "### 1.1 `stock_items`\n"
    "| column | type | notes |\n"
    "|--------|------|-------|\n"
    "| id     | UUID PK | gen_random_uuid() |\n"
    "| sku    | varchar(64) | unique per warehouse |\n"
    "### 1.2 `warehouses`\n"
    "| column | type | notes |\n"
    "|--------|------|-------|\n"
    "| id     | UUID PK | gen_random_uuid() |\n"
    "| code   | varchar(10) | unique |\n"
)


@pytest.fixture()
def approved_foundation_design(db_session, nex_horizont, zoltan) -> DesignDocument:
    """Persist an **approved** Foundation DESIGN.md for NEX Horizont.

    BEHAVIOR.md §3.9 step 2 (system response) requires the context to
    contain the Foundation DESIGN.md. The ``module_id IS NULL`` /
    ``doc_type='design'`` combination is DESIGN.md §1.5's definition
    of a Foundation document; ``approved_by`` is stamped so the
    §3.9 precondition "DESIGN.md modulu existuje a je schválený" is
    satisfied at the project level too (the UI fetches both layers
    before opening the Architect).
    """
    doc = DesignDocument(
        project_id=nex_horizont.id,
        module_id=None,  # Foundation = project-level.
        doc_type="design",
        content=FOUNDATION_DESIGN_MD,
        version=1,
        approved_by=zoltan.id,
    )
    db_session.add(doc)
    db_session.flush()
    return doc


@pytest.fixture()
def approved_stk_design(db_session, nex_horizont, stk_in_design, tibor) -> DesignDocument:
    """Persist an **approved** DESIGN.md for the STK module.

    BEHAVIOR.md §3.9 precondition line 2: "DESIGN.md modulu existuje
    a je schválený (pre modul-level session)". The row mirrors the
    §3.5 postcondition: ``approved_by`` populated (``ri``-role user
    who clicked "Schváliť DESIGN.md") and ``doc_type='design'``.
    """
    doc = DesignDocument(
        project_id=nex_horizont.id,
        module_id=stk_in_design.id,
        doc_type="design",
        content=STK_DESIGN_MD,
        version=1,
        approved_by=tibor.id,
    )
    db_session.add(doc)
    db_session.flush()
    return doc


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.9 end-to-end.
# ---------------------------------------------------------------------------


class TestStartArchitectSessionHappyPath:
    """End-to-end walkthrough of workflow §3.9 against the real app."""

    def test_full_workflow_opens_module_architect_for_stk(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        stk_in_design,
        gsc_done,
        approved_foundation_design,
        approved_stk_design,
    ):
        """Drive steps 1-5 of the workflow and verify every postcondition.

        Reproduces the §3.9 worked example faithfully: Tibor opens the
        Architect for STK, the UI verifies the DESIGN.md is approved
        and no other active session exists, the session is created
        with ``status='active'``, Tibor's user turn is persisted, and
        the assistant turn is persisted with token counts and cost
        after the streamed reply completes.
        """
        # --- Step 1 (UI side): Tibor clicks "Otvoriť Architect". The
        # UI checks that STK's DESIGN.md is approved — §3.9
        # precondition line 2 — by listing the most recent design
        # document row for STK with ``approved_by`` populated.
        design_resp = client.get(
            "/api/v1/design-documents",
            params={
                "project_id": str(nex_horizont.id),
                "module_id": str(stk_in_design.id),
                "doc_type": "design",
            },
        )
        assert design_resp.status_code == 200, design_resp.text
        design_rows = design_resp.json()["items"]
        assert len(design_rows) >= 1
        # The newest DESIGN.md is approved — the §3.9 precondition.
        newest_design = design_rows[0]
        assert newest_design["module_id"] == str(stk_in_design.id)
        assert newest_design["approved_by"] is not None

        # --- Step 1 (UI side, cont.): the "one Architect per module"
        # invariant is observable via the active-session filter. The
        # UI refuses to show "Otvoriť Architect" until this returns
        # zero rows.
        pre_active_resp = client.get(
            "/api/v1/architect-sessions",
            params={
                "module_id": str(stk_in_design.id),
                "status": "active",
            },
        )
        assert pre_active_resp.status_code == 200
        assert pre_active_resp.json()["total"] == 0

        # --- Step 2 (system): the system assembles the context for
        # the Architect. The HTTP-observable side is: Foundation
        # DESIGN.md exists, module DESIGN.md exists, the module
        # registry is populated with GSC (``done``) and STK
        # (``in_design``). The Qdrant / KB retrieval is orchestration
        # territory.
        foundation_resp = client.get(
            "/api/v1/design-documents",
            params={
                "project_id": str(nex_horizont.id),
                "doc_type": "design",
            },
        )
        assert foundation_resp.status_code == 200
        foundation_ids = {row["id"] for row in foundation_resp.json()["items"]}
        assert str(approved_foundation_design.id) in foundation_ids
        assert str(approved_stk_design.id) in foundation_ids

        registry_resp = client.get(
            "/api/v1/project-modules",
            params={"project_id": str(nex_horizont.id)},
        )
        assert registry_resp.status_code == 200
        registry_by_code = {row["code"]: row for row in registry_resp.json()["items"]}
        assert registry_by_code["GSC"]["status"] == "done"
        assert registry_by_code["STK"]["status"] == "in_design"

        # --- Step 3 (system): the session is created with
        # ``status='active'``. ``created_by`` is Tibor (the
        # ``ri_senior`` actor from the §3.9 worked example).
        create_resp = client.post(
            "/api/v1/architect-sessions",
            json={
                "project_id": str(nex_horizont.id),
                "module_id": str(stk_in_design.id),
                "created_by": str(tibor.id),
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        session_body = create_resp.json()
        session_id = session_body["id"]
        assert session_body["project_id"] == str(nex_horizont.id)
        assert session_body["module_id"] == str(stk_in_design.id)
        assert session_body["created_by"] == str(tibor.id)
        # §3.9 step 3 / postcondition line 1: ``status='active'``.
        assert session_body["status"] == "active"
        # ``closed_at`` is NULL on a freshly-opened session.
        assert session_body["closed_at"] is None
        # Server-generated fields populated.
        assert session_body["id"]
        assert session_body["created_at"]
        assert session_body["updated_at"]

        # The "one Architect per module" invariant has flipped: the
        # active-session filter now returns exactly this row.
        post_active_resp = client.get(
            "/api/v1/architect-sessions",
            params={
                "module_id": str(stk_in_design.id),
                "status": "active",
            },
        )
        assert post_active_resp.status_code == 200
        assert post_active_resp.json()["total"] == 1
        assert post_active_resp.json()["items"][0]["id"] == session_id

        # --- Step 4 (Tibor): the user turn is persisted before the
        # assistant replies. The §3.9 worked example prompt is
        # "Vygeneruj implementačný plán pre STK modul".
        user_prompt = "Vygeneruj implementačný plán pre STK modul"
        user_msg_resp = client.post(
            "/api/v1/architect-messages",
            json={
                "session_id": session_id,
                "role": "user",
                "content": user_prompt,
            },
        )
        assert user_msg_resp.status_code == 201, user_msg_resp.text
        user_msg = user_msg_resp.json()
        assert user_msg["session_id"] == session_id
        assert user_msg["role"] == "user"
        assert user_msg["content"] == user_prompt
        # User turns carry no token counts / cost — only the model
        # reply has a measurable cost (§3.9 step 5).
        assert user_msg["input_tokens"] is None
        assert user_msg["output_tokens"] is None
        assert user_msg["cost_usd"] is None

        # --- Step 5 (system): after the SSE stream completes, the
        # assistant turn is persisted with the final token counts
        # and USD cost. The §3.9 "Konkrétny príklad" says the reply
        # is an EPIC with 4 FEATs; the content is orchestration
        # territory, but the accounting columns are the observable
        # postcondition (§3.9 line 3: "Token spotreba a cost
        # zaznamenané").
        assistant_reply = (
            "Navrhujem EPIC 'STK — Skladové karty zásob' so 4 FEATmi: "
            "1. DB + Alembic migration, 2. Service layer, "
            "3. Router + API, 4. Frontend."
        )
        assistant_msg_resp = client.post(
            "/api/v1/architect-messages",
            json={
                "session_id": session_id,
                "role": "assistant",
                "content": assistant_reply,
                "input_tokens": 4210,
                "output_tokens": 812,
                "cost_usd": "0.094860",
            },
        )
        assert assistant_msg_resp.status_code == 201, assistant_msg_resp.text
        assistant_msg = assistant_msg_resp.json()
        assert assistant_msg["session_id"] == session_id
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == assistant_reply
        assert assistant_msg["input_tokens"] == 4210
        assert assistant_msg["output_tokens"] == 812
        # ``cost_usd`` is serialised as a decimal string by the
        # Pydantic ``Decimal`` field — compare with ``Decimal`` so
        # trailing-zero / formatting choices do not fail the test.
        assert Decimal(assistant_msg["cost_usd"]) == Decimal("0.094860")

        # --- Postcondition verification (HTTP) -------------------------
        # 1. The session is active and addressable by primary key.
        session_show = client.get(f"/api/v1/architect-sessions/{session_id}")
        assert session_show.status_code == 200
        assert session_show.json()["status"] == "active"
        assert session_show.json()["module_id"] == str(stk_in_design.id)

        # 2. The conversation is readable in order (user first,
        #    assistant second) via the session-scoped message list.
        transcript = client.get(
            "/api/v1/architect-messages",
            params={"session_id": session_id},
        )
        assert transcript.status_code == 200
        assert transcript.json()["total"] == 2
        roles_in_order = [row["role"] for row in transcript.json()["items"]]
        assert roles_in_order == ["user", "assistant"]

        # 3. Cost accounting is observable via the ``role=assistant``
        #    filter — the exact query the Reports page uses to
        #    compute session token totals (DESIGN.md §3.2
        #    ``ProjectMetricsCard``).
        assistant_only = client.get(
            "/api/v1/architect-messages",
            params={"session_id": session_id, "role": "assistant"},
        )
        assert assistant_only.status_code == 200
        assert assistant_only.json()["total"] == 1
        assert Decimal(assistant_only.json()["items"][0]["cost_usd"]) == Decimal("0.094860")

        # --- Postcondition verification (DB state) ---------------------
        db_session.expire_all()

        # 1. ``architect_sessions`` row persisted with the §3.9 fields.
        persisted_session = db_session.get(ArchitectSession, uuid.UUID(session_id))
        assert persisted_session is not None
        assert persisted_session.project_id == nex_horizont.id
        assert persisted_session.module_id == stk_in_design.id
        assert persisted_session.created_by == tibor.id
        # §3.9 postcondition line 1: ``status='active'``.
        assert persisted_session.status == "active"
        assert persisted_session.closed_at is None

        # 2. Both messages are persisted and attributed to the session.
        persisted_user = db_session.get(ArchitectMessage, uuid.UUID(user_msg["id"]))
        persisted_assistant = db_session.get(
            ArchitectMessage,
            uuid.UUID(assistant_msg["id"]),
        )
        assert persisted_user is not None
        assert persisted_user.session_id == persisted_session.id
        assert persisted_user.role == "user"
        assert persisted_user.content == user_prompt

        assert persisted_assistant is not None
        assert persisted_assistant.session_id == persisted_session.id
        assert persisted_assistant.role == "assistant"
        assert persisted_assistant.content == assistant_reply

        # 3. §3.9 postcondition line 3: "Token spotreba a cost
        #    zaznamenané" — the ``Numeric(10, 6)`` column round-trips
        #    as a :class:`~decimal.Decimal` with the exact value
        #    supplied.
        assert persisted_assistant.input_tokens == 4210
        assert persisted_assistant.output_tokens == 812
        assert persisted_assistant.cost_usd == Decimal("0.094860")
        # The user turn's accounting columns stay NULL.
        assert persisted_user.input_tokens is None
        assert persisted_user.output_tokens is None
        assert persisted_user.cost_usd is None

    def test_ri_director_may_also_open_architect_for_another_module(
        self,
        client,
        db_session,
        zoltan,
        nex_horizont,
    ):
        """Zoltán (``ri_director``) is an equally valid actor per §3.9.

        BEHAVIOR.md §3.9 lists "[[actor:ri_director]],
        [[actor:ri_senior]], [[actor:ha_medior]]" — all three are
        acceptable. The happy path is Tibor's; this test pins
        Zoltán's equivalence by opening a session for a *different*
        module (PAB, ``Katalóg partnerov``) so the "one active
        session per module" invariant is respected across both
        tests when the fixture graph is reused.
        """
        # Seed a second module that belongs only to this test — no
        # cross-test interference with STK.
        pab = ProjectModule(
            project_id=nex_horizont.id,
            code="PAB",
            name="Katalóg partnerov",
            category="Katalógy",
            status="in_design",
        )
        db_session.add(pab)
        db_session.flush()

        resp = client.post(
            "/api/v1/architect-sessions",
            json={
                "project_id": str(nex_horizont.id),
                "module_id": str(pab.id),
                "created_by": str(zoltan.id),
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["created_by"] == str(zoltan.id)
        assert body["module_id"] == str(pab.id)
        assert body["status"] == "active"

        db_session.expire_all()
        persisted = db_session.get(ArchitectSession, uuid.UUID(body["id"]))
        assert persisted is not None
        assert persisted.created_by == zoltan.id

    def test_ha_medior_may_also_open_architect_for_another_module(
        self,
        client,
        db_session,
        peter,
        nex_horizont,
    ):
        """Peter (``ha_medior``) is an equally valid actor per §3.9.

        BEHAVIOR.md §3.9 explicitly lists ``ha_medior`` alongside the
        two ``ri`` roles. ``ha_medior`` maps to ``role='ha'`` at the
        DB level; the router accepts any project member identically
        regardless of role. Tested against a third module
        (``NAK``, ``Nákupné objednávky``) so the one-active-session
        invariant is respected.
        """
        nak = ProjectModule(
            project_id=nex_horizont.id,
            code="NAK",
            name="Nákupné objednávky",
            category="Nákup",
            status="in_design",
        )
        db_session.add(nak)
        db_session.flush()

        resp = client.post(
            "/api/v1/architect-sessions",
            json={
                "project_id": str(nex_horizont.id),
                "module_id": str(nak.id),
                "created_by": str(peter.id),
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["created_by"] == str(peter.id)
        assert body["module_id"] == str(nak.id)
        assert body["status"] == "active"

        db_session.expire_all()
        persisted = db_session.get(ArchitectSession, uuid.UUID(body["id"]))
        assert persisted is not None
        assert persisted.created_by == peter.id

    def test_project_level_foundation_session_also_allowed(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
    ):
        """``module_id=None`` opens a Foundation / project-level session.

        DESIGN.md §1.5: "NULL = Foundation/project session". The §3.9
        worked example is module-level, but a project-level session
        is a legitimate UI operation (Foundation DESIGN.md reviews,
        cross-module questions). This test pins that the HTTP
        surface accepts the project-level variant identically — so a
        future refactor does not accidentally make ``module_id``
        non-optional.
        """
        resp = client.post(
            "/api/v1/architect-sessions",
            json={
                "project_id": str(nex_horizont.id),
                # module_id omitted → Foundation-level session.
                "created_by": str(tibor.id),
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["project_id"] == str(nex_horizont.id)
        assert body["module_id"] is None
        assert body["status"] == "active"

        # The module-scoped active-session filter does **not**
        # surface this row — a project-level session is not a
        # module-level one, so the "one Architect per module"
        # invariant for any individual module is untouched.
        module_filtered = client.get(
            "/api/v1/architect-sessions",
            params={
                "project_id": str(nex_horizont.id),
                "module_id": str(uuid.uuid4()),
                "status": "active",
            },
        )
        assert module_filtered.status_code == 200
        assert module_filtered.json()["total"] == 0


# ---------------------------------------------------------------------------
# Edge cases — Pydantic literal rejection, missing session on GET,
# session close round-trip.
# ---------------------------------------------------------------------------


class TestStartArchitectSessionEdgeCases:
    """Edge cases around the §3.9 workflow's entry and close surfaces.

    The three guards below back the ``ck_architect_sessions_status``
    DB CHECK (invalid literal → HTTP 422 one layer shallower at the
    Pydantic boundary), the §3.9 precondition "Modul existuje v
    projekte" (missing session on GET → HTTP 404), and the §3.9
    close-path postcondition that a subsequent PATCH to ``closed``
    auto-stamps ``closed_at`` and frees the "one Architect per module"
    UI gate for the next invocation.
    """

    def test_invalid_status_value_is_rejected_with_422(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        stk_in_design,
    ):
        """POST with a non-literal status → HTTP 422, no row persisted.

        :data:`ArchitectSessionStatus` is a Pydantic ``Literal`` of
        the two allowed values (``active | closed``); a bogus string
        fails schema validation before the service sees it. No row
        may be persisted — the active-session count stays at zero.
        """
        resp = client.post(
            "/api/v1/architect-sessions",
            json={
                "project_id": str(nex_horizont.id),
                "module_id": str(stk_in_design.id),
                "created_by": str(tibor.id),
                "status": "open",  # not in the Literal.
            },
        )
        assert resp.status_code == 422, resp.text

        # No ``architect_sessions`` row was created — the
        # active-session filter stays at zero.
        listing = client.get(
            "/api/v1/architect-sessions",
            params={"module_id": str(stk_in_design.id), "status": "active"},
        )
        assert listing.status_code == 200
        assert listing.json()["total"] == 0

    def test_get_on_missing_session_returns_404(self, client):
        """GET against a random UUID → HTTP 404.

        The service-layer ``get_by_id`` raises
        ``ValueError("ArchitectSession {id} not found")``; the
        router's ``_map_value_error`` translates the "not found"
        substring into HTTP 404.
        """
        missing_id = uuid.uuid4()
        resp = client.get(f"/api/v1/architect-sessions/{missing_id}")
        assert resp.status_code == 404, resp.text
        assert "not found" in resp.json()["detail"].lower()

    def test_session_close_autostamps_closed_at_and_frees_module_slot(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        stk_in_design,
    ):
        """PATCH ``status='closed'`` auto-stamps ``closed_at`` and frees the module slot.

        The §3.9 "one Architect per module" precondition is
        evaluated against ``architect_sessions.status='active'`` ∧
        ``module_id=<stk>``. A session in ``closed`` state is
        invisible to that filter, so closing frees the slot for the
        next §3.9 invocation. The service's
        ``active → closed`` auto-stamp (mirroring the ``resolved``
        pattern in :mod:`backend.services.bug`) populates
        ``closed_at = now()`` when the caller omits it.
        """
        # Open the session first — this is the §3.9 happy path in
        # miniature.
        create_resp = client.post(
            "/api/v1/architect-sessions",
            json={
                "project_id": str(nex_horizont.id),
                "module_id": str(stk_in_design.id),
                "created_by": str(tibor.id),
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        session_id = create_resp.json()["id"]
        assert create_resp.json()["closed_at"] is None

        # The module slot is occupied while the session is active.
        occupied = client.get(
            "/api/v1/architect-sessions",
            params={"module_id": str(stk_in_design.id), "status": "active"},
        )
        assert occupied.status_code == 200
        assert occupied.json()["total"] == 1

        # Close the session without supplying ``closed_at`` — the
        # service must auto-stamp it.
        close_resp = client.patch(
            f"/api/v1/architect-sessions/{session_id}",
            json={"status": "closed"},
        )
        assert close_resp.status_code == 200, close_resp.text
        closed_body = close_resp.json()
        assert closed_body["status"] == "closed"
        # Auto-stamped by the service — non-null after the
        # transition.
        assert closed_body["closed_at"] is not None

        # The active-session filter for STK now returns zero — the
        # "one Architect per module" UI gate is free again.
        freed = client.get(
            "/api/v1/architect-sessions",
            params={"module_id": str(stk_in_design.id), "status": "active"},
        )
        assert freed.status_code == 200
        assert freed.json()["total"] == 0

        # The ``status=closed`` filter surfaces the session — a
        # closed session is still addressable for historical
        # transcript review.
        archived = client.get(
            "/api/v1/architect-sessions",
            params={"module_id": str(stk_in_design.id), "status": "closed"},
        )
        assert archived.status_code == 200
        assert archived.json()["total"] == 1
        assert archived.json()["items"][0]["id"] == session_id

        # DB state agrees with the HTTP payload.
        db_session.expire_all()
        persisted = db_session.get(ArchitectSession, uuid.UUID(session_id))
        assert persisted is not None
        assert persisted.status == "closed"
        assert persisted.closed_at is not None

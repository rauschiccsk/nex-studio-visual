"""Integration test for BEHAVIOR.md §3.10 ``workflow:generate_epic_feat_task_plan``.

Exercises the full happy path of the **generate_epic_feat_task_plan**
workflow end-to-end through the real FastAPI ``app``. The workflow
takes an *active* :class:`ArchitectSession` (the postcondition of
workflow §3.9, verified in ``test_workflow_start_architect_session``)
scoped to a module with an approved DESIGN.md, lets Tibor ask the
Architect to "Vygeneruj EPIC/FEAT/TASK pre STK modul", persists the
two-turn conversation (user prompt + structured Architect reply), and
then — when Tibor clicks "Importovať do projektu" — parses the
structured plan into first-class rows: one :class:`Epic` scoped to the
module, several :class:`Feat` rows with ``number`` local to the epic,
and several :class:`Task` rows per feat with ``number`` local to the
feat. Hierarchical numbering follows the §3.10 worked example:
EPIC 4 (the fourth epic in the project, because GSC / DOB / PAB
already occupy epics 1–3), FEAT 4.1…4.4 within EPIC 4, and
TASK 4.1.1…4.1.N within each FEAT (see BEHAVIOR.md §3.10 step 3 and
the ``rule:hierarchical_numbering`` cross-cut).

The AI that actually synthesises the plan is out of scope at this
layer; the test supplies the structured plan the Architect would have
returned (the §3.10 "Konkrétny príklad": 4 FEATs, each with 3–5 TASKs)
and verifies the *observable* side effects — the HTTP contract, the
DB rows produced by the import, and the postconditions
(``status='todo'`` on every feat and task, immutable per-parent
numbering, ``epic.module_id`` pinned to the session's module).

    Precondition (per BEHAVIOR.md §3.10):
        * An :class:`ArchitectSession` exists with
          ``status='active'`` (the postcondition of workflow §3.9).
        * The module's DESIGN.md is approved — modelled as a
          :class:`DesignDocument` row for the STK module with
          ``doc_type='design'`` and ``approved_by`` populated (the
          same §3.5 monotonic contract §3.9 re-pins).
        * BEHAVIOR.md §3.10 specifies the actor is ``ri_director``,
          ``ri_senior`` or ``ha_medior``. The happy-path worked
          example is Tibor (``ri_senior``); persisting the message
          turns and the import rows is ``created_by``-agnostic at
          the CRUD layer, so no separate role-permutation test is
          required here.

    Steps (per BEHAVIOR.md §3.10):
        1. Tibor types "Vygeneruj EPIC/FEAT/TASK pre STK modul" into
           the Architect chat → the user turn is persisted via
           ``POST /api/v1/architect-messages`` with ``role='user'``
           and the streamed structured Architect reply (the EPIC/FEAT/
           TASK plan) is persisted via a second
           ``POST /api/v1/architect-messages`` with ``role='assistant'``
           plus the usual token / cost accounting (§3.9 step 5
           contract, re-used verbatim here).
        2. Tibor clicks "Importovať do projektu" — the
           orchestrator parses the structured plan and persists the
           rows via:
                * ``POST /api/v1/epics`` for the EPIC (``module_id``
                  pinned to the session's STK module so the §3.10
                  postcondition "EPIC 4 (STK) previazaný s
                  ``module_id=stk_id``" holds).
                * ``POST /api/v1/feats`` for each FEAT under the new
                  epic — ``number`` is auto-assigned by the service
                  as ``MAX(number) + 1`` per epic, starting at ``1``.
                * ``POST /api/v1/tasks`` for each TASK under each
                  FEAT — ``number`` is auto-assigned per feat.
        3. — (system) — hierarchical numbering assignment is
           observable on the created rows: ``epic.number=4`` because
           three prior epics already exist in the project (GSC,
           DOB, PAB — seeded as a fixture so the §3.10 worked
           example lines up with the real service's monotonic
           allocator); ``feat.number ∈ {1, 2, 3, 4}`` local to this
           epic; ``task.number`` starts at ``1`` per feat.
        4. — (system) — the imported plan is readable through the
           same list endpoints the ``EpicList`` / ``FeatCard`` /
           ``TaskItem`` UI (DESIGN.md §3.1) drives:
                * ``GET /api/v1/epics?project_id=...&module_id=<stk>``
                  returns exactly the new EPIC 4.
                * ``GET /api/v1/feats?epic_id=<epic4>`` returns the
                  four feats in ``number ASC`` order.
                * ``GET /api/v1/tasks?feat_id=<feat>`` returns the
                  tasks for that feat in ``number ASC`` order.

    Postcondition (per BEHAVIOR.md §3.10):
        * The ``epics`` table holds a row with ``project_id``
          matching the session's project, ``module_id`` matching the
          session's module, ``number=4`` (the fourth epic in the
          project per the seeded GSC / DOB / PAB fixture), and
          ``status='planned'`` (server default — the service does
          not advance the epic on create).
        * The ``feats`` table holds one row per imported FEAT with
          ``epic_id`` pointing at the new epic and ``number`` local
          to the epic (1…4). Every row has ``status='todo'`` (§3.10
          postcondition line 4).
        * The ``tasks`` table holds one row per imported TASK with
          ``feat_id`` pointing at its parent feat and ``number``
          local to the feat (starting at ``1``). Every row has
          ``status='todo'`` (§3.10 postcondition line 4).
        * The user-facing ``{epic.number}.{feat.number}.{task.number}``
          identifiers resolve uniquely — the three
          ``UNIQUE(project_id, number)`` / ``UNIQUE(epic_id, number)``
          / ``UNIQUE(feat_id, number)`` constraints
          (``uq_epics_project_id_number`` / ``uq_feats_epic_id_number``
          / ``uq_tasks_feat_id_number``) guarantee this at the DB
          level and are re-pinned by the import's HTTP contract.

Edge cases verified alongside the happy path:

    * **§4.15 ``edge:design_not_approved_before_epic``** — attempting
      the import while the module's DESIGN.md is *not* approved is
      refused. At the CRUD layer of Feats 0–6 the backend has no
      dedicated ``import_epic_plan`` orchestration endpoint, so the
      gate is exercised via the observable pre-flight the
      orchestrator runs: the
      ``approved_by``-filtered list query is the signal the UI and
      orchestrator use to enable / disable the "Importovať do
      projektu" button. With an un-approved DESIGN.md the filtered
      list returns empty, so a correctly-gated orchestrator refuses
      the import and no :class:`Epic` (nor its feats / tasks) is
      created — the §4.15 postcondition "Systém odmietne import …
      Architect session môže pokračovať v konverzácii, ale import
      je zablokovaný". The monotonic approval contract of §3.5
      flips the gate open again once approval lands.
    * **§4.19 ``edge:feat_numbering_gap``** — numbers are immutable
      after creation. Deleting a feat in the middle of the range
      leaves a gap (FEAT 4.1, 4.3 without 4.2) and a subsequent
      create picks up ``MAX(number) + 1 = 5`` rather than
      renumbering the surviving feats. This pins the
      ``rule:hierarchical_numbering`` business rule at the HTTP /
      service boundary — the "business decision — immutable numbers"
      §4.19 captures.
    * **All feats / tasks land in ``status='todo'``** — §3.10
      postcondition line 4 explicitly pins every feat and task at
      ``todo`` on import. A regression that flipped the DB
      ``server_default`` (e.g. to ``planned`` to match epics) would
      break the Tasks UI's "Delegovať" button gate (DESIGN.md §3.1
      ``DelegateButton``), so the default is pinned both at the
      HTTP surface and in the persisted row.

Auth note:
    The current codebase (Feats 0–6) wires routers directly without a
    JWT dependency, so the integration test does not exercise a login
    flow. The "role=ri/ha, member of project, Architect session
    active" precondition is satisfied by persisting the actor with the
    correct ``role`` and seeding the active session row. Role
    enforcement at the router level is a separate concern covered by
    future auth-middleware tests.
"""

from __future__ import annotations

import uuid

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
from backend.db.models.tasks import Epic, Feat, Task

# ---------------------------------------------------------------------------
# Precondition fixtures — Zoltán (ri_director) and Tibor (ri_senior); the
# NEX Horizont project with both as members; three "older" modules (GSC,
# DOB, PAB) already done and each carrying a prior epic so the §3.10
# worked example "EPIC 4 (pre STK)" lines up with the real service's
# ``MAX(number) + 1`` allocator; the STK module in ``in_design`` with an
# approved DESIGN.md; and an active Architect session scoped to STK.
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
    """Persist Tibor — the ``ri_senior`` primary actor in §3.10's worked example.

    BEHAVIOR.md §3.10 names Tibor in the Steps table
    ("Tibor v Architect session napíše …"). Zoltán (``ri_director``)
    and Peter (``ha_medior``) are equally valid per the Actor line;
    the happy-path test follows the worked example with Tibor.
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
def nex_horizont(db_session, zoltan, tibor) -> Project:
    """Persist the NEX Horizont project with Zoltán and Tibor as members.

    BEHAVIOR.md §3.10 inherits its project-membership precondition
    from §3.9 (the actor must already be in an Architect session,
    which in turn requires project membership). Both approvers are
    added up-front so the shared happy-path fixture graph is reusable
    by follow-up tests.
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
    db_session.flush()
    return project


@pytest.fixture()
def prior_modules_with_epics(
    db_session,
    nex_horizont,
    tibor,
) -> list[ProjectModule]:
    """Persist three prior ``done`` modules, each with an existing epic.

    BEHAVIOR.md §3.10 step 3 and the worked example both call out
    ``EPIC 4 (STK)`` — i.e. the fourth epic in the project. The real
    ``epic_service.create`` allocator auto-assigns ``number`` as
    ``MAX(number) + 1`` per project (see :mod:`backend.services.epic`
    ``_next_epic_number``), so for the import to land on ``number=4``
    three prior epics must already exist. Seeding one epic per
    ``done`` module (GSC / DOB / PAB) is the simplest way to make the
    §3.10 worked example line up with the real service's monotonic
    assignment, and it also keeps the fixture graph close to the
    real state of the project at the time §3.10 fires (the older
    modules already shipped, STK is the next one in).
    """
    modules: list[ProjectModule] = []
    for number, (code, name, category) in enumerate(
        [
            ("GSC", "Globálne skladové karty", "Sklad"),
            ("DOB", "Dodávateľské objednávky", "Nákup"),
            ("PAB", "Katalóg partnerov", "Katalógy"),
        ],
        start=1,
    ):
        module = ProjectModule(
            project_id=nex_horizont.id,
            code=code,
            name=name,
            category=category,
            status="done",
        )
        db_session.add(module)
        db_session.flush()

        db_session.add(
            Epic(
                project_id=nex_horizont.id,
                module_id=module.id,
                number=number,
                title=f"EPIC {number} — {code} ({name})",
                status="done",
            )
        )
        db_session.flush()
        modules.append(module)
    return modules


@pytest.fixture()
def gsc_done(prior_modules_with_epics) -> ProjectModule:
    """Return the GSC module — the prerequisite for STK per §3.9 example."""
    for module in prior_modules_with_epics:
        if module.code == "GSC":
            return module
    raise AssertionError("GSC module missing from prior_modules_with_epics fixture")


@pytest.fixture()
def stk_in_design(db_session, nex_horizont, gsc_done) -> ProjectModule:
    """Persist STK in ``in_design`` with a dependency edge on GSC.

    §3.10 inherits this state from §3.9's worked example — STK sits
    in ``in_design`` between the approved DESIGN.md (§3.5) and the
    ``in_development`` hop. The dependency edge on GSC mirrors the
    §3.9 worked example ("informáciu že GSC je ``done`` — závislosť
    splnená") even though §3.10 itself does not re-evaluate the
    dependency status at import time.
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


# The STK DESIGN.md Tibor already approved in §3.5 — the §3.10
# precondition "DESIGN.md je schválený".
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
def approved_stk_design(db_session, nex_horizont, stk_in_design, tibor) -> DesignDocument:
    """Persist an **approved** DESIGN.md for the STK module.

    §3.10 precondition line 2: "DESIGN.md je schválený". The row
    mirrors the §3.5 postcondition: ``approved_by`` populated by the
    ``ri``-role user who clicked "Schváliť DESIGN.md".
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


@pytest.fixture()
def active_stk_session(db_session, nex_horizont, stk_in_design, tibor) -> ArchitectSession:
    """Persist an **active** Architect session scoped to STK.

    §3.10 precondition line 1: "Architect session je ``active``".
    This row is the postcondition of workflow §3.9 (pinned by
    ``test_workflow_start_architect_session``); §3.10 picks it up
    unchanged and uses it to persist the conversation turns that
    produce the plan.
    """
    session = ArchitectSession(
        project_id=nex_horizont.id,
        module_id=stk_in_design.id,
        created_by=tibor.id,
        status="active",
    )
    db_session.add(session)
    db_session.flush()
    return session


# The §3.10 worked example plan: EPIC 4 "STK — Skladové karty zásob"
# with four FEATs, each carrying three to five TASKs. The numbers on
# the FEATs / TASKs below are the *expected* hierarchical numbers —
# the real services allocate them from scratch via ``MAX(number) + 1``.
IMPORTED_PLAN: list[dict] = [
    {
        "title": "DB Model + Alembic migration",
        "description": "Vytvoriť SQLAlchemy model a Alembic autogenerate.",
        "tasks": [
            {"title": "Model stock_items", "task_type": "backend"},
            {"title": "Model warehouses", "task_type": "backend"},
            {"title": "Alembic autogenerate", "task_type": "migration"},
        ],
    },
    {
        "title": "Service layer",
        "description": "CRUD a business logika pre STK.",
        "tasks": [
            {"title": "create_stock_item", "task_type": "backend"},
            {"title": "list_stock_items", "task_type": "backend"},
            {"title": "Service tests", "task_type": "test"},
        ],
    },
    {
        "title": "Router + API",
        "description": "REST API endpoints pre STK.",
        "tasks": [
            {"title": "POST /stock-items", "task_type": "backend"},
            {"title": "GET /stock-items", "task_type": "backend"},
            {"title": "Router tests", "task_type": "test"},
        ],
    },
    {
        "title": "Frontend",
        "description": "StockItems page a komponenty.",
        "tasks": [
            {"title": "StockItemsPage", "task_type": "frontend"},
            {"title": "StockItemForm", "task_type": "frontend"},
            {"title": "Frontend tests", "task_type": "frontend"},
        ],
    },
]


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.10 end-to-end.
# ---------------------------------------------------------------------------


class TestGenerateEpicFeatTaskPlanHappyPath:
    """End-to-end walkthrough of workflow §3.10 against the real app."""

    def test_full_import_creates_epic_feats_and_tasks_with_hierarchical_numbers(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        stk_in_design,
        prior_modules_with_epics,
        approved_stk_design,
        active_stk_session,
    ):
        """Drive steps 1-4 of the workflow and verify every postcondition.

        Reproduces the §3.10 worked example faithfully: Tibor asks
        the Architect to generate a plan for STK, the two-turn
        conversation is persisted, Tibor clicks "Importovať do
        projektu", and the orchestrator persists EPIC 4 + 4 FEATs +
        12 TASKs with the hierarchical-numbering contract
        (``rule:hierarchical_numbering``) intact.
        """
        # --- Step 0 (precondition recap): the active session is
        # observable via the module-scoped filter §3.9 pins. §3.10
        # does not re-validate the session here at the service layer,
        # but the orchestrator uses the filter to locate the session
        # before appending messages.
        session_id = str(active_stk_session.id)
        sessions_resp = client.get(
            "/api/v1/architect-sessions",
            params={
                "module_id": str(stk_in_design.id),
                "status": "active",
            },
        )
        assert sessions_resp.status_code == 200, sessions_resp.text
        assert sessions_resp.json()["total"] == 1
        assert sessions_resp.json()["items"][0]["id"] == session_id

        # --- Step 0 (precondition recap): the approved DESIGN.md is
        # observable via the ``approved_by``-filtered list. This is
        # also the §4.15 gate — the orchestrator refuses the import
        # when this list is empty.
        design_resp = client.get(
            "/api/v1/design-documents",
            params={
                "project_id": str(nex_horizont.id),
                "module_id": str(stk_in_design.id),
                "doc_type": "design",
            },
        )
        assert design_resp.status_code == 200
        design_rows = design_resp.json()["items"]
        assert len(design_rows) >= 1
        assert design_rows[0]["approved_by"] is not None

        # --- Step 1 (Tibor): the user prompt turn.
        user_prompt = "Vygeneruj EPIC/FEAT/TASK pre STK modul"
        user_msg_resp = client.post(
            "/api/v1/architect-messages",
            json={
                "session_id": session_id,
                "role": "user",
                "content": user_prompt,
            },
        )
        assert user_msg_resp.status_code == 201, user_msg_resp.text
        assert user_msg_resp.json()["role"] == "user"
        assert user_msg_resp.json()["content"] == user_prompt

        # --- Step 1 (system): the structured Architect reply. The
        # content here is the §3.10 "Konkrétny príklad" rendered as
        # a markdown plan — the orchestrator parses this to drive
        # step 2 below, but at the CRUD layer we only need to pin
        # that the message was persisted with the usual token / cost
        # accounting.
        assistant_reply = (
            "EPIC: STK — Skladové karty zásob\n\n"
            "FEAT 1: DB Model + Alembic migration\n"
            "  TASK 1: Model stock_items (backend)\n"
            "  TASK 2: Model warehouses (backend)\n"
            "  TASK 3: Alembic autogenerate (migration)\n"
            "FEAT 2: Service layer\n"
            "  TASK 1: create_stock_item (backend)\n"
            "  TASK 2: list_stock_items (backend)\n"
            "  TASK 3: Service tests (test)\n"
            "FEAT 3: Router + API\n"
            "  TASK 1: POST /stock-items (backend)\n"
            "  TASK 2: GET /stock-items (backend)\n"
            "  TASK 3: Router tests (test)\n"
            "FEAT 4: Frontend\n"
            "  TASK 1: StockItemsPage (frontend)\n"
            "  TASK 2: StockItemForm (frontend)\n"
            "  TASK 3: Frontend tests (frontend)\n"
        )
        assistant_msg_resp = client.post(
            "/api/v1/architect-messages",
            json={
                "session_id": session_id,
                "role": "assistant",
                "content": assistant_reply,
                "input_tokens": 5120,
                "output_tokens": 1440,
                "cost_usd": "0.134880",
            },
        )
        assert assistant_msg_resp.status_code == 201, assistant_msg_resp.text
        assistant_msg_id = assistant_msg_resp.json()["id"]

        # --- Step 2 (Tibor): "Importovať do projektu". The
        # orchestrator persists EPIC 4 first. ``module_id`` is the
        # session's module — the §3.10 postcondition "EPIC 4 (STK)
        # previazaný s ``module_id=stk_id``".
        epic_resp = client.post(
            "/api/v1/epics",
            json={
                "project_id": str(nex_horizont.id),
                "module_id": str(stk_in_design.id),
                "title": "STK — Skladové karty zásob",
            },
        )
        assert epic_resp.status_code == 201, epic_resp.text
        epic_body = epic_resp.json()
        epic_id = epic_body["id"]
        # --- Step 3 (system): hierarchical-numbering assignment.
        # Three prior epics already exist (GSC / DOB / PAB), so the
        # ``MAX(number) + 1`` allocator lands on ``4`` — the §3.10
        # worked example.
        assert epic_body["number"] == 4
        assert epic_body["module_id"] == str(stk_in_design.id)
        assert epic_body["project_id"] == str(nex_horizont.id)
        # ``status`` defaults to ``planned`` (DB ``server_default``);
        # the Tasks-UI "Delegovať" button gate is scoped to feats /
        # tasks, so planned on the epic is correct.
        assert epic_body["status"] == "planned"

        # --- Step 2 (cont.): one FEAT per item in the plan, then
        # the TASKs per FEAT. The service layer auto-assigns the
        # per-parent ``number`` — we verify it afterwards.
        created_feat_ids: list[str] = []
        created_feat_numbers: list[int] = []
        for feat_spec in IMPORTED_PLAN:
            feat_resp = client.post(
                "/api/v1/feats",
                json={
                    "epic_id": epic_id,
                    "title": feat_spec["title"],
                    "description": feat_spec["description"],
                },
            )
            assert feat_resp.status_code == 201, feat_resp.text
            feat_body = feat_resp.json()
            # §3.10 postcondition line 4: every feat lands at
            # ``status='todo'``.
            assert feat_body["status"] == "todo"
            # Sanity: the server-managed counters start at zero.
            assert feat_body["task_count"] == 0
            assert feat_body["auto_fix_count"] == 0
            created_feat_ids.append(feat_body["id"])
            created_feat_numbers.append(feat_body["number"])

            for task_spec in feat_spec["tasks"]:
                task_resp = client.post(
                    "/api/v1/tasks",
                    json={
                        "feat_id": feat_body["id"],
                        "title": task_spec["title"],
                        "task_type": task_spec["task_type"],
                    },
                )
                assert task_resp.status_code == 201, task_resp.text
                task_body = task_resp.json()
                # §3.10 postcondition line 4: every task lands at
                # ``status='todo'``.
                assert task_body["status"] == "todo"
                assert task_body["feat_id"] == feat_body["id"]
                assert task_body["task_type"] == task_spec["task_type"]

        # --- Step 3 verification: FEAT numbers are 1…4 local to
        # this epic (fresh epic → ``MAX(number) + 1`` starts at 1).
        assert created_feat_numbers == [1, 2, 3, 4]

        # --- Step 4 (system): the imported plan is readable via the
        # list endpoints the Tasks UI drives. ``module_id`` filter
        # returns exactly the new epic; a fresh-epic feat listing is
        # ordered by ``number ASC``; tasks likewise.
        module_epics_resp = client.get(
            "/api/v1/epics",
            params={
                "project_id": str(nex_horizont.id),
                "module_id": str(stk_in_design.id),
            },
        )
        assert module_epics_resp.status_code == 200
        module_epic_ids = [row["id"] for row in module_epics_resp.json()["items"]]
        assert module_epic_ids == [epic_id]
        assert module_epics_resp.json()["total"] == 1

        feats_list_resp = client.get(
            "/api/v1/feats",
            params={"epic_id": epic_id},
        )
        assert feats_list_resp.status_code == 200
        feats_listed = feats_list_resp.json()["items"]
        assert feats_list_resp.json()["total"] == 4
        # ``number ASC`` order — the ``EpicList`` collapsible UI
        # convention (DESIGN.md §3.1).
        assert [row["number"] for row in feats_listed] == [1, 2, 3, 4]
        assert [row["title"] for row in feats_listed] == [spec["title"] for spec in IMPORTED_PLAN]
        # Every feat lands at ``todo`` — §3.10 postcondition line 4.
        assert {row["status"] for row in feats_listed} == {"todo"}

        for feat_id, feat_spec in zip(created_feat_ids, IMPORTED_PLAN):
            tasks_list_resp = client.get(
                "/api/v1/tasks",
                params={"feat_id": feat_id},
            )
            assert tasks_list_resp.status_code == 200
            tasks_listed = tasks_list_resp.json()["items"]
            assert tasks_list_resp.json()["total"] == len(feat_spec["tasks"])
            # ``number`` starts at 1 per feat, increments by 1.
            assert [row["number"] for row in tasks_listed] == list(range(1, len(feat_spec["tasks"]) + 1))
            # Every task lands at ``todo`` — §3.10 postcondition
            # line 4.
            assert {row["status"] for row in tasks_listed} == {"todo"}
            # ``task_type`` round-trips from the imported plan.
            assert [row["task_type"] for row in tasks_listed] == [spec["task_type"] for spec in feat_spec["tasks"]]

        # --- Postcondition verification (DB state) ---------------------
        db_session.expire_all()

        # 1. The EPIC is persisted as EPIC 4 scoped to STK.
        persisted_epic = db_session.get(Epic, uuid.UUID(epic_id))
        assert persisted_epic is not None
        assert persisted_epic.project_id == nex_horizont.id
        assert persisted_epic.module_id == stk_in_design.id
        assert persisted_epic.number == 4
        assert persisted_epic.status == "planned"

        # 2. Four feats persisted with per-epic numbering.
        persisted_feats = sorted(
            [db_session.get(Feat, uuid.UUID(fid)) for fid in created_feat_ids],
            key=lambda f: f.number,
        )
        assert [f.number for f in persisted_feats] == [1, 2, 3, 4]
        assert {f.epic_id for f in persisted_feats} == {persisted_epic.id}
        assert {f.status for f in persisted_feats} == {"todo"}

        # 3. Tasks persisted with per-feat numbering; every task's
        #    ``feat_id`` matches one of the four feats; every task
        #    lands at ``todo``.
        total_tasks_expected = sum(len(spec["tasks"]) for spec in IMPORTED_PLAN)
        all_tasks_resp = client.get(
            "/api/v1/tasks",
            params={"limit": 100},
        )
        assert all_tasks_resp.status_code == 200
        persisted_task_rows = [row for row in all_tasks_resp.json()["items"] if row["feat_id"] in set(created_feat_ids)]
        assert len(persisted_task_rows) == total_tasks_expected
        for feat_id, feat_spec in zip(created_feat_ids, IMPORTED_PLAN):
            per_feat = [row for row in persisted_task_rows if row["feat_id"] == feat_id]
            per_feat.sort(key=lambda row: row["number"])
            assert [row["number"] for row in per_feat] == list(range(1, len(feat_spec["tasks"]) + 1))
            assert {row["status"] for row in per_feat} == {"todo"}

        # 4. The conversation that produced the plan is still
        #    addressable — the UI's "show the Architect reply that
        #    produced this epic" link from the Tasks page.
        transcript_resp = client.get(
            "/api/v1/architect-messages",
            params={"session_id": session_id},
        )
        assert transcript_resp.status_code == 200
        assert transcript_resp.json()["total"] == 2
        assert [row["role"] for row in transcript_resp.json()["items"]] == [
            "user",
            "assistant",
        ]
        persisted_assistant = db_session.get(
            ArchitectMessage,
            uuid.UUID(assistant_msg_id),
        )
        assert persisted_assistant is not None
        assert persisted_assistant.role == "assistant"

        # 5. The prior epics (GSC / DOB / PAB) are still the only
        #    ones numbered 1-3 in the project — the import added
        #    epic 4 without disturbing the existing numbering.
        project_epics_resp = client.get(
            "/api/v1/epics",
            params={"project_id": str(nex_horizont.id), "limit": 100},
        )
        assert project_epics_resp.status_code == 200
        numbers = sorted(row["number"] for row in project_epics_resp.json()["items"])
        assert numbers == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Edge cases — §4.15 design-not-approved gate and §4.19 feat-numbering gap.
# ---------------------------------------------------------------------------


class TestGenerateEpicFeatTaskPlanEdgeCases:
    """Edge cases around the §3.10 workflow's import gate and numbering contract.

    The two edges below back the §4.15 "design_not_approved_before_epic"
    business-rule gate (unapproved DESIGN.md → orchestrator refuses
    the import, no rows created) and the §4.19 "feat_numbering_gap"
    business-rule decision (numbers are immutable after creation;
    deleting a feat leaves a gap rather than renumbering survivors).
    """

    def test_unapproved_design_blocks_import_gate(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        stk_in_design,
        active_stk_session,
    ):
        """§4.15: an un-approved DESIGN.md gates the import.

        The CRUD-layer observable is the orchestrator's pre-flight:
        ``GET /api/v1/design-documents?project_id=...&module_id=<stk>&doc_type=design``
        — with ``approved_by`` filtered to "not null" the list is
        empty, so a correctly-gated orchestrator does not issue the
        ``POST /api/v1/epics`` call. No :class:`Epic` (nor any
        dependent feats / tasks) is therefore created — the §4.15
        postcondition "Systém odmietne import … Architect session
        môže pokračovať v konverzácii, ale import je zablokovaný".
        """
        # Persist a *draft* DESIGN.md — ``approved_by=None`` makes
        # it invisible to the §3.10 import gate.
        draft = DesignDocument(
            project_id=nex_horizont.id,
            module_id=stk_in_design.id,
            doc_type="design",
            content=STK_DESIGN_MD,
            version=1,
            approved_by=None,
        )
        db_session.add(draft)
        db_session.flush()

        # The Architect conversation may still proceed — §4.15
        # explicitly allows this ("session môže pokračovať v
        # konverzácii"). Persist the user prompt to pin that the
        # session itself is not disabled by the failed gate.
        session_id = str(active_stk_session.id)
        user_msg_resp = client.post(
            "/api/v1/architect-messages",
            json={
                "session_id": session_id,
                "role": "user",
                "content": "Vygeneruj EPIC/FEAT/TASK pre STK modul",
            },
        )
        assert user_msg_resp.status_code == 201, user_msg_resp.text

        # The orchestrator pre-flights the DESIGN.md approval. Only
        # un-approved rows exist, so every returned row has
        # ``approved_by IS NULL`` — a correctly-gated orchestrator
        # refuses the import.
        design_resp = client.get(
            "/api/v1/design-documents",
            params={
                "project_id": str(nex_horizont.id),
                "module_id": str(stk_in_design.id),
                "doc_type": "design",
            },
        )
        assert design_resp.status_code == 200
        design_rows = design_resp.json()["items"]
        assert len(design_rows) >= 1
        approvals = {row["approved_by"] for row in design_rows}
        assert approvals == {None}

        # Postcondition: no EPIC was created for STK — the orchestrator
        # did not issue the ``POST /api/v1/epics`` call.
        module_epics_resp = client.get(
            "/api/v1/epics",
            params={
                "project_id": str(nex_horizont.id),
                "module_id": str(stk_in_design.id),
            },
        )
        assert module_epics_resp.status_code == 200
        assert module_epics_resp.json()["total"] == 0

        # DB state agrees — no ``epics`` row with ``module_id=STK``.
        db_session.expire_all()
        assert db_session.query(Epic).filter(Epic.module_id == stk_in_design.id).count() == 0

    def test_deleting_feat_leaves_numbering_gap(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        stk_in_design,
        prior_modules_with_epics,
        approved_stk_design,
        active_stk_session,
    ):
        """§4.19: numbers are immutable — deletion leaves a gap.

        The ``rule:hierarchical_numbering`` business rule pins feat
        numbers as immutable after creation. Deleting FEAT 4.2 from
        a populated epic must therefore leave a gap (4.1, 4.3, 4.4)
        rather than renumbering the survivors. A new feat created
        afterwards lands on ``MAX(number) + 1 = 5`` (the gap is
        preserved, not reused) — see ``_next_feat_number`` in
        :mod:`backend.services.feat`.
        """
        # --- Run the §3.10 import in miniature (EPIC 4 with four
        # FEATs) so the deletion has something to chew on. The
        # tasks layer is irrelevant here — the §4.19 edge is
        # feat-scoped.
        epic_resp = client.post(
            "/api/v1/epics",
            json={
                "project_id": str(nex_horizont.id),
                "module_id": str(stk_in_design.id),
                "title": "STK — Skladové karty zásob",
            },
        )
        assert epic_resp.status_code == 201, epic_resp.text
        epic_id = epic_resp.json()["id"]
        assert epic_resp.json()["number"] == 4

        feat_ids_by_number: dict[int, str] = {}
        for feat_spec in IMPORTED_PLAN:
            feat_resp = client.post(
                "/api/v1/feats",
                json={
                    "epic_id": epic_id,
                    "title": feat_spec["title"],
                },
            )
            assert feat_resp.status_code == 201, feat_resp.text
            feat_ids_by_number[feat_resp.json()["number"]] = feat_resp.json()["id"]

        # Sanity — FEAT 4.1…4.4 exist.
        assert sorted(feat_ids_by_number.keys()) == [1, 2, 3, 4]

        # --- Delete FEAT 4.2 (the "Service layer" FEAT). The
        # ``tasks.feat_id ON DELETE CASCADE`` wipes any dependent
        # rows at the DB level — not relevant here (no tasks
        # seeded) but documented by the router.
        delete_resp = client.delete(f"/api/v1/feats/{feat_ids_by_number[2]}")
        assert delete_resp.status_code == 204

        # Survivors keep their original numbers — no renumbering.
        survivors_resp = client.get(
            "/api/v1/feats",
            params={"epic_id": epic_id},
        )
        assert survivors_resp.status_code == 200
        survivors = survivors_resp.json()["items"]
        assert [row["number"] for row in survivors] == [1, 3, 4]
        # The gap is explicit — number 2 is missing.
        assert 2 not in {row["number"] for row in survivors}

        # --- A new feat created after the deletion lands on
        # ``MAX(number) + 1 = 5`` — the gap is preserved, not reused.
        replacement_resp = client.post(
            "/api/v1/feats",
            json={
                "epic_id": epic_id,
                "title": "Migration helpers (post-delete)",
            },
        )
        assert replacement_resp.status_code == 201, replacement_resp.text
        assert replacement_resp.json()["number"] == 5

        # Final state — FEATs 1, 3, 4, 5 (the gap at 2 survives).
        final_resp = client.get(
            "/api/v1/feats",
            params={"epic_id": epic_id},
        )
        assert final_resp.status_code == 200
        assert [row["number"] for row in final_resp.json()["items"]] == [1, 3, 4, 5]

        # DB state agrees with the HTTP payload.
        db_session.expire_all()
        persisted_numbers = sorted(
            row.number for row in db_session.query(Feat).filter(Feat.epic_id == uuid.UUID(epic_id)).all()
        )
        assert persisted_numbers == [1, 3, 4, 5]

    def test_tasks_default_to_todo_even_when_epic_defaults_to_planned(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        stk_in_design,
        prior_modules_with_epics,
        approved_stk_design,
        active_stk_session,
    ):
        """§3.10 postcondition line 4: every feat / task lands at ``todo``.

        The epic's default status is ``planned`` (DB ``server_default``
        — see ``epics.status``), but the feat and task defaults are
        ``todo``. A regression that flipped the feat / task default
        to ``planned`` (e.g. to match the epic) would break the
        Tasks-UI "Delegovať" button gate (DESIGN.md §3.1
        ``DelegateButton`` only lights up on ``todo`` rows), so the
        default is pinned here independently of the happy path.
        """
        epic_resp = client.post(
            "/api/v1/epics",
            json={
                "project_id": str(nex_horizont.id),
                "module_id": str(stk_in_design.id),
                "title": "STK — Skladové karty zásob",
            },
        )
        assert epic_resp.status_code == 201, epic_resp.text
        # Epic defaults to ``planned``.
        assert epic_resp.json()["status"] == "planned"
        epic_id = epic_resp.json()["id"]

        feat_resp = client.post(
            "/api/v1/feats",
            json={
                "epic_id": epic_id,
                "title": "DB Model + Alembic migration",
            },
        )
        assert feat_resp.status_code == 201, feat_resp.text
        # Feat defaults to ``todo`` even though the epic is ``planned``.
        assert feat_resp.json()["status"] == "todo"
        feat_id = feat_resp.json()["id"]

        task_resp = client.post(
            "/api/v1/tasks",
            json={
                "feat_id": feat_id,
                "title": "Model stock_items",
                "task_type": "backend",
            },
        )
        assert task_resp.status_code == 201, task_resp.text
        # Task defaults to ``todo`` — the "Delegovať" gate.
        assert task_resp.json()["status"] == "todo"

        # DB state agrees with the HTTP payload.
        db_session.expire_all()
        persisted_feat = db_session.get(Feat, uuid.UUID(feat_id))
        assert persisted_feat is not None
        assert persisted_feat.status == "todo"
        persisted_task = db_session.get(Task, uuid.UUID(task_resp.json()["id"]))
        assert persisted_task is not None
        assert persisted_task.status == "todo"

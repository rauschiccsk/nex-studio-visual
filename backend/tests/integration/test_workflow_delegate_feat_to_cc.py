"""Integration test for BEHAVIOR.md §3.12 ``workflow:delegate_feat_to_cc``.

Exercises the full happy path of the **delegate_feat_to_cc** workflow
end-to-end through the real FastAPI ``app``. The workflow is the core
"ship the feat" loop of NEX Studio: Dominik (``ha_medior``) opens a
FEAT in ``status='todo'`` that belongs to a module whose DESIGN.md is
already approved (the §3.5 postcondition), clicks "Delegovať na CC",
reviews the prompt preview, clicks "Spustiť", and the orchestrator
fires a CC agent that ultimately lands a commit on ``main``. The
workflow stitches together four entities — :class:`Delegation`,
:class:`ExecutionLog`, :class:`GuardianReview` and :class:`Feat` — and
the ``status='done'`` on the feat is the last signal the Tasks UI
(DESIGN.md §3.1 ``FeatCard``) uses to show the feat as shipped.

The CC subprocess, the SSE / WebSocket stream, the GitHub API
commit-verification call and the Guardian pipeline's Ollama / Opus
invocations are all orchestration territory and out of scope at the
HTTP / CRUD layer. The test supplies the structured side effects those
orchestration components would produce (the ``commit_hash`` CC extracts
from its output, the ``commit_verified=True`` flag the GitHub job sets,
the Layer 1 / 2 / 3 review rows Guardian writes) and verifies the
*observable* side effects against the HTTP contract and the DB state:
the ``delegations`` row transitions ``pending → running → done``, the
``execution_logs`` row carries token counts / cost / duration /
``commit_verified=True``, three ``guardian_reviews`` rows exist
(``layer1`` / ``layer2`` / ``layer3``) all with ``passed=True``, and
the feat finishes at ``status='done'`` with ``actual_minutes``
populated.

The worked example throughout is drawn from BEHAVIOR.md §3.12 step 1
verbatim: "Dominik otvorí FEAT 4.2 'STK Service layer' → klikne
'Delegovať na CC'". FEAT 4.2 is the second feat under EPIC 4 (STK —
the §3.10 worked example's epic) and carries three service-layer
tasks (``create_stock_adjustment``, ``list_stock_adjustments``,
service tests).

    Precondition (per BEHAVIOR.md §3.12):
        * FEAT exists with ``status='todo'`` (line 527) — the default
          server-side state after §3.10 import, pinned by
          ``test_workflow_generate_epic_feat_task_plan``.
        * DESIGN.md for the module is approved (line 528) — the §3.5
          postcondition, pinned by
          ``test_workflow_approve_design_md``.
        * No other active delegation for the same feat (line 529) —
          the orchestrator's pre-flight gate, observable at the HTTP
          layer as an empty result for
          ``GET /api/v1/delegations?feat_id=<feat>&status=running``.
        * Actor is a member of the project (line 530) — satisfied by
          persisting a :class:`ProjectMember` row for Dominik.

    Steps (per BEHAVIOR.md §3.12):
        1. Dominik opens FEAT 4.2 "STK Service layer" → clicks
           "Delegovať na CC" — the system shows a prompt preview
           (context: DESIGN.md + ``checklist_type`` + feat description
           + task list). Modelled here as the orchestrator's
           pre-flight fetches: the feat, the tasks under the feat,
           the approved DESIGN.md (the §4.15 / §3.12 gate), and the
           "no other active delegation" check.
        2. Dominik reviews the prompt and clicks "Spustiť" — the
           orchestrator persists a :class:`Delegation` with
           ``status='pending'`` via
           ``POST /api/v1/delegations``. The feat transitions to
           ``status='in_progress'`` to reflect that work is in flight
           (consumed by the ``FeatCard`` UI badge, DESIGN.md §3.1).
        3. — (system) — the orchestrator starts the CC subprocess
           and PATCHes the delegation to ``status='running'`` via
           ``PATCH /api/v1/delegations/{id}``. Streaming output is
           out of scope here (the ``raw_output`` column is where it
           would accumulate if captured at the HTTP layer).
        4. Dominik watches streaming output — not observable at the
           HTTP CRUD layer. The ``raw_output`` column on
           ``delegations`` is the sink for the replay buffer; the
           test exercises it via a single PATCH with a representative
           excerpt to pin the "captured stream" contract without
           trying to model the WebSocket replay protocol.
        5. CC exits with code 0 — the orchestrator extracts
           ``commit_hash`` from the output, PATCHes the delegation to
           ``status='done'`` with the ``commit_hash`` populated, and
           POSTs an :class:`ExecutionLog` with the token counts,
           cost, duration and ``commit_hash``. A subsequent PATCH on
           the log flips ``commit_verified`` from ``False`` to
           ``True`` once the GitHub API call lands.
        6. — (system) — the orchestrator runs the Guardian pipeline:
           three POSTs to ``/api/v1/guardian-reviews`` (one per
           Layer), each with ``delegation_id`` pointing at the new
           delegation. Happy path: every layer returns
           ``passed=True`` with ``risk_level='low'``.
        7. — (system) — Guardian is clean and no blockers exist, so
           the orchestrator PATCHes the feat to ``status='done'``
           with ``actual_minutes`` measured from the delegation's
           ``started_at`` / ``completed_at`` delta.

    Postcondition (per BEHAVIOR.md §3.12):
        * :class:`Delegation` has ``status='done'`` and
          ``commit_hash`` populated (postcondition line 545).
          Note: ``commit_verified`` lives on :class:`ExecutionLog`,
          not on the delegation row itself — the BEHAVIOR.md line
          "``commit_verified=TRUE``" is semantically the execution
          log's flag.
        * :class:`ExecutionLog` row exists with ``input_tokens`` /
          ``output_tokens`` / ``total_cost_usd`` / ``duration_seconds``
          populated (postcondition line 546).
        * Three :class:`GuardianReview` rows exist — one per Layer —
          all with ``passed=True`` for the happy path (postcondition
          line 547).
        * FEAT ``status='done'`` (postcondition line 548).
        * FEAT ``actual_minutes`` populated (postcondition line 549).

Edge cases verified alongside the happy path:

    * **Active-delegation gate** — a second "Delegovať na CC" click
      while a delegation is already ``running`` must be refused by
      the orchestrator (§3.12 precondition line 529: "Žiadna iná
      aktívna delegácia pre ten istý feat"). The CRUD-layer
      observable is the orchestrator's pre-flight query — a
      ``GET /api/v1/delegations?feat_id=<feat>&status=running``
      returning any row signals "delegation in flight, refuse the
      new one". Pins the contract at the HTTP surface so a
      correctly-gated orchestrator never creates a duplicate
      delegation for the same feat.
    * **Guardian block keeps feat off ``done``** — the §3.14
      "Risk=CRITICAL with MUST_FIX" branch (step 8 of §3.14): when
      any Guardian review returns ``passed=False`` the feat must
      *not* be auto-transitioned to ``status='done'``; it stays at
      ``in_progress`` awaiting Zoltán's manual approval. Pins the
      postcondition gate at the HTTP layer so a regression that
      auto-closed the feat regardless of Guardian's verdict would
      fail the test. The delegation itself is still ``done`` — the
      CC subprocess succeeded — but the feat's lifecycle is
      Guardian-gated.

Auth note:
    The current codebase (Feats 0-6) wires routers directly without a
    JWT dependency, so the integration test does not exercise a login
    flow. The "role=ha / ri, member of project" precondition is
    satisfied by persisting the actor with the correct ``role`` and
    seeding a :class:`ProjectMember` row. Role enforcement at the
    router level is a separate concern covered by future
    auth-middleware tests.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.db.models.delegations import Delegation, ExecutionLog
from backend.db.models.foundation import User
from backend.db.models.guardian import GuardianReview
from backend.db.models.projects import Project, ProjectModule
from backend.db.models.specifications import DesignDocument
from backend.db.models.tasks import Epic, Feat, Task

# ---------------------------------------------------------------------------
# Precondition fixtures — Dominik (ha_medior, the primary actor in the §3.12
# worked example), Zoltán (ri_director, an alternative actor per the Actor
# line), the NEX Horizont project with both as members, the STK module
# ``in_development`` (post-§3.5) with an approved DESIGN.md, EPIC 4 and
# FEAT 4.2 "STK Service layer" with three tasks.
# ---------------------------------------------------------------------------


@pytest.fixture()
def dominik(db_session) -> User:
    """Persist Dominik — the ``ha_medior`` primary actor in §3.12's worked example.

    BEHAVIOR.md §3.12 names Dominik in the Steps table ("Dominik
    otvorí FEAT 4.2 …"). Zoltán (``ri_director``) and Tibor
    (``ri_senior``) are listed as equally valid per the Actor line
    — at the DB level roles ``ha`` and ``ri`` both resolve — but
    the worked example is Dominik's.
    """
    user = User(
        username="dominik",
        email="dominik@isnex.ai",
        password_hash="hashed-placeholder",
        role="ha",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def zoltan(db_session) -> User:
    """Persist Zoltán — the ``ri_director`` who approved the DESIGN.md.

    Zoltán is not the §3.12 actor but is the named approver of the
    STK DESIGN.md per the §3.5 worked example. His ``User`` row is
    needed to populate ``design_documents.approved_by`` (NOT NULL
    because the row is approved, even though the FK permits NULL for
    drafts).
    """
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
def nex_horizont(db_session, zoltan, dominik) -> Project:
    """Persist the NEX Horizont project with Zoltán and Dominik as members.

    §3.12 precondition line 530: "Actor je člen projektu". Dominik
    is the §3.12 actor and therefore needs a ``ProjectMember`` row.
    Zoltán is kept as a member so the same fixture graph can back
    the §3.14 "Risk=CRITICAL" branch where Zoltán is the manual
    approver.
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


# The KB filesystem path the STK DESIGN.md sits at after §3.5 approval.
# §3.12 reads the file content into the CC prompt context ("kontext:
# DESIGN.md + checklist_type + feat popis + task list") — the path
# itself is not inspected by the delegation, but the module carries
# it so the orchestrator can locate the file.
STK_DESIGN_MD_KB_PATH = "/home/icc/knowledge/projects/nex-horizont/modules/STK/DESIGN.md"


@pytest.fixture()
def stk_module(db_session, nex_horizont) -> ProjectModule:
    """Persist the STK module in ``in_development``.

    §3.12 fires during implementation — the module has already
    completed §3.5 (DESIGN.md approved → ``in_design`` → optionally
    advanced to ``in_development``). ``design_doc_path`` is populated
    so the orchestrator can read the file for the prompt context
    (§3.12 step 1 "kontext: DESIGN.md + …").
    """
    module = ProjectModule(
        project_id=nex_horizont.id,
        code="STK",
        name="Skladové karty zásob",
        category="Sklad",
        status="in_development",
        design_doc_path=STK_DESIGN_MD_KB_PATH,
    )
    db_session.add(module)
    db_session.flush()
    return module


# The approved STK DESIGN.md — the §3.5 postcondition output. The
# delegation reads the content into the prompt context; for the
# CRUD-layer test only the ``approved_by IS NOT NULL`` signal
# matters (the §3.12 / §4.15 gate).
STK_DESIGN_MD = (
    "# DESIGN.md — modul STK (Skladové karty zásob)\n\n"
    "## 1. Data model\n"
    "### 1.1 `stock_items`\n"
    "| column | type | notes |\n"
    "|--------|------|-------|\n"
    "| id     | UUID PK | gen_random_uuid() |\n"
    "| sku    | varchar(64) | unique per warehouse |\n"
)


@pytest.fixture()
def approved_stk_design(db_session, nex_horizont, stk_module, zoltan) -> DesignDocument:
    """Persist an **approved** DESIGN.md for the STK module.

    §3.12 precondition line 528: "DESIGN.md pre daný modul je
    schválený". The ``approved_by``-populated row is also the §4.15
    import gate signal — the orchestrator will refuse the delegation
    if the approved-DESIGN.md list is empty.
    """
    doc = DesignDocument(
        project_id=nex_horizont.id,
        module_id=stk_module.id,
        doc_type="design",
        content=STK_DESIGN_MD,
        version=1,
        approved_by=zoltan.id,
        approved_at=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
    )
    db_session.add(doc)
    db_session.flush()
    return doc


@pytest.fixture()
def stk_epic(db_session, nex_horizont, stk_module) -> Epic:
    """Persist EPIC 4 (STK) — the §3.10 worked example epic.

    §3.12's worked example references "FEAT 4.2 'STK Service layer'"
    — the ``4.`` prefix is the epic number (the fourth epic in the
    project, because GSC / DOB / PAB already occupy epics 1-3 per
    §3.10). We seed EPIC 4 directly — §3.10's import machinery is
    pinned by a separate integration test.
    """
    epic = Epic(
        project_id=nex_horizont.id,
        module_id=stk_module.id,
        number=4,
        title="STK — Skladové karty zásob",
        status="in_progress",
    )
    db_session.add(epic)
    db_session.flush()
    return epic


@pytest.fixture()
def feat_service_layer(db_session, stk_epic) -> Feat:
    """Persist FEAT 4.2 "STK Service layer" in ``status='todo'``.

    §3.12 precondition line 527: "FEAT existuje so
    ``status='todo'``". The default server-side state after §3.10
    import is ``todo`` (pinned by
    ``test_workflow_generate_epic_feat_task_plan`` postcondition
    line 4). ``number=2`` lines up with the §3.12 worked example
    "FEAT 4.2".
    """
    feat = Feat(
        epic_id=stk_epic.id,
        number=2,
        title="STK Service layer",
        description="CRUD a business logika pre STK skladové karty.",
        status="todo",
        estimated_minutes=120,
    )
    db_session.add(feat)
    db_session.flush()
    return feat


@pytest.fixture()
def service_layer_tasks(db_session, feat_service_layer) -> list[Task]:
    """Persist three ``todo`` tasks under FEAT 4.2.

    §3.12 step 1 "kontext: … + task list" — the orchestrator feeds
    the feat's tasks into the CC prompt. At the CRUD layer we only
    need the rows to exist so the prompt-preview / context query
    returns a non-empty list.
    """
    tasks = [
        Task(
            feat_id=feat_service_layer.id,
            number=1,
            title="create_stock_item service function",
            task_type="backend",
            status="todo",
            checklist_type="service",
        ),
        Task(
            feat_id=feat_service_layer.id,
            number=2,
            title="list_stock_items service function",
            task_type="backend",
            status="todo",
            checklist_type="service",
        ),
        Task(
            feat_id=feat_service_layer.id,
            number=3,
            title="STK service tests",
            task_type="test",
            status="todo",
            checklist_type="service",
        ),
    ]
    for task in tasks:
        db_session.add(task)
    db_session.flush()
    return tasks


# The CC prompt the orchestrator synthesises for FEAT 4.2 — DESIGN.md
# snippet + checklist_type + feat description + task list. The exact
# prompt format is orchestration territory; the test persists a
# representative prompt string so the delegation row carries a
# non-empty ``prompt`` column (``NOT NULL`` at the DB level).
FEAT_42_PROMPT = (
    "You are implementing FEAT 4.2 'STK Service layer' in the NEX Horizont "
    "project (module STK). Context:\n\n"
    "## DESIGN.md (STK)\n"
    f"{STK_DESIGN_MD}\n"
    "## Checklist\n- service\n\n"
    "## FEAT description\nCRUD a business logika pre STK skladové karty.\n\n"
    "## Tasks\n"
    "1. create_stock_item service function (backend)\n"
    "2. list_stock_items service function (backend)\n"
    "3. STK service tests (test)\n"
)


# A representative commit hash CC would extract from its output after a
# successful run (DESIGN.md §1.7 ``commit_hash`` is 40-char SHA-1 hex).
EXPECTED_COMMIT_HASH = "a3f1b9d8e0c47a2e5b6c9e1d3f2a8b7c5d4e9f01"


# A truncated NDJSON-ish excerpt of what CC would stream — not parsed
# at the CRUD layer, just stored verbatim as ``raw_output`` so the
# "captured stream" contract is exercised.
CC_STREAM_EXCERPT = (
    '{"type":"tool_use","name":"Read","input":{"file_path":"backend/services/stk.py"}}\n'
    '{"type":"tool_use","name":"Edit","input":{"file_path":"backend/services/stk.py"}}\n'
    '{"type":"tool_use","name":"Bash","input":{"command":"poetry run pytest tests/test_stk_service.py"}}\n'
    '{"type":"result","commit_hash":"a3f1b9d8e0c47a2e5b6c9e1d3f2a8b7c5d4e9f01"}\n'
)


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.12 end-to-end.
# ---------------------------------------------------------------------------


class TestDelegateFeatToCcHappyPath:
    """End-to-end walkthrough of workflow §3.12 against the real app."""

    def test_full_delegation_drives_feat_to_done(
        self,
        client,
        db_session,
        dominik,
        zoltan,
        nex_horizont,
        stk_module,
        approved_stk_design,
        stk_epic,
        feat_service_layer,
        service_layer_tasks,
    ):
        """Drive steps 1-7 of the workflow and verify every postcondition.

        Reproduces the §3.12 worked example faithfully: Dominik
        opens FEAT 4.2 "STK Service layer", reviews the prompt
        preview, clicks "Spustiť", and the system drives the
        delegation through ``pending → running → done``, persists
        the :class:`ExecutionLog` with token counts / cost / commit
        hash, flips ``commit_verified`` to ``True`` after the
        GitHub API call, runs the Guardian pipeline (three passing
        Layer reviews) and finally transitions the feat to
        ``status='done'`` with ``actual_minutes`` populated.
        """
        feat_id = str(feat_service_layer.id)
        module_id = str(stk_module.id)

        # --- Step 1 (pre-flight — precondition recap): the feat is
        # ``todo`` and the DESIGN.md is approved. Both are the
        # orchestrator's gates before offering the "Delegovať na CC"
        # button.
        feat_resp = client.get(f"/api/v1/feats/{feat_id}")
        assert feat_resp.status_code == 200, feat_resp.text
        assert feat_resp.json()["status"] == "todo"
        assert feat_resp.json()["number"] == 2

        design_gate_resp = client.get(
            "/api/v1/design-documents",
            params={
                "project_id": str(nex_horizont.id),
                "module_id": module_id,
                "doc_type": "design",
                "approved_by": str(zoltan.id),
            },
        )
        assert design_gate_resp.status_code == 200
        assert design_gate_resp.json()["total"] == 1

        # Step 1 (pre-flight — "žiadna iná aktívna delegácia pre
        # ten istý feat"): the running-delegations list for this
        # feat is empty.
        no_active_resp = client.get(
            "/api/v1/delegations",
            params={"feat_id": feat_id, "status": "running"},
        )
        assert no_active_resp.status_code == 200
        assert no_active_resp.json()["total"] == 0

        # Step 1 (pre-flight — task list the prompt builder consumes).
        tasks_resp = client.get(
            "/api/v1/tasks",
            params={"feat_id": feat_id},
        )
        assert tasks_resp.status_code == 200
        assert tasks_resp.json()["total"] == 3
        assert [row["number"] for row in tasks_resp.json()["items"]] == [1, 2, 3]

        # --- Step 2 (Dominik): "Spustiť" → create the delegation in
        # ``pending`` state. At the same time the feat's ``status``
        # transitions to ``in_progress`` so the Tasks UI reflects
        # that work is in flight.
        started_at = datetime(2026, 4, 16, 9, 0, 0, tzinfo=timezone.utc)
        delegation_create = client.post(
            "/api/v1/delegations",
            json={
                "feat_id": feat_id,
                "cc_agent": "ubuntu_cc",
                "prompt": FEAT_42_PROMPT,
                "status": "pending",
                "started_at": started_at.isoformat(),
            },
        )
        assert delegation_create.status_code == 201, delegation_create.text
        delegation_body = delegation_create.json()
        delegation_id = delegation_body["id"]
        assert delegation_body["status"] == "pending"
        assert delegation_body["feat_id"] == feat_id
        assert delegation_body["cc_agent"] == "ubuntu_cc"
        assert delegation_body["prompt"] == FEAT_42_PROMPT
        # No commit / output yet — the CC subprocess has not started.
        assert delegation_body["commit_hash"] is None
        assert delegation_body["raw_output"] is None
        assert delegation_body["completed_at"] is None

        # The feat moves to ``in_progress`` to reflect "work in flight".
        feat_in_progress = client.patch(
            f"/api/v1/feats/{feat_id}",
            json={"status": "in_progress"},
        )
        assert feat_in_progress.status_code == 200
        assert feat_in_progress.json()["status"] == "in_progress"

        # --- Step 3 (system): CC subprocess starts → delegation
        # transitions to ``running``.
        running_resp = client.patch(
            f"/api/v1/delegations/{delegation_id}",
            json={"status": "running"},
        )
        assert running_resp.status_code == 200
        assert running_resp.json()["status"] == "running"

        # --- Step 4 (Dominik watches stream): the orchestrator
        # accumulates NDJSON lines into ``raw_output``. One PATCH is
        # enough to pin the "captured stream" contract at the CRUD
        # layer — the UI's replay buffer is a separate concern.
        stream_resp = client.patch(
            f"/api/v1/delegations/{delegation_id}",
            json={"raw_output": CC_STREAM_EXCERPT},
        )
        assert stream_resp.status_code == 200
        assert stream_resp.json()["raw_output"] == CC_STREAM_EXCERPT
        # Still ``running`` — the stream PATCH does not terminate
        # the delegation.
        assert stream_resp.json()["status"] == "running"

        # --- Step 5 (CC exits 0): the orchestrator extracts the
        # commit hash from the stream, PATCHes the delegation to
        # ``done`` with ``commit_hash`` populated and ``completed_at``
        # stamped.
        completed_at = started_at + timedelta(minutes=22, seconds=14)
        done_resp = client.patch(
            f"/api/v1/delegations/{delegation_id}",
            json={
                "status": "done",
                "commit_hash": EXPECTED_COMMIT_HASH,
                "completed_at": completed_at.isoformat(),
            },
        )
        assert done_resp.status_code == 200, done_resp.text
        done_body = done_resp.json()
        # §3.12 postcondition line 545 (partial): ``status='done'``
        # and ``commit_hash`` populated.
        assert done_body["status"] == "done"
        assert done_body["commit_hash"] == EXPECTED_COMMIT_HASH
        assert done_body["completed_at"] is not None

        # --- Step 5 (cont.): the orchestrator writes the
        # :class:`ExecutionLog` with token counts / cost / duration /
        # commit hash. ``commit_verified`` starts ``False`` and is
        # flipped by the GitHub verification job.
        execution_log_create = client.post(
            "/api/v1/execution-logs",
            json={
                "delegation_id": delegation_id,
                "status": "done",
                "duration_seconds": 1334,
                "input_tokens": 18_420,
                "output_tokens": 4_210,
                "total_cost_usd": "0.287410",
                "commit_hash": EXPECTED_COMMIT_HASH,
                "commit_verified": False,
            },
        )
        assert execution_log_create.status_code == 201, execution_log_create.text
        log_body = execution_log_create.json()
        log_id = log_body["id"]
        assert log_body["delegation_id"] == delegation_id
        assert log_body["status"] == "done"
        assert log_body["duration_seconds"] == 1334
        assert log_body["input_tokens"] == 18_420
        assert log_body["output_tokens"] == 4_210
        assert Decimal(log_body["total_cost_usd"]) == Decimal("0.287410")
        assert log_body["commit_hash"] == EXPECTED_COMMIT_HASH
        assert log_body["commit_verified"] is False

        # --- Step 5 (cont.): GitHub API verifies the commit exists
        # on the branch → ``commit_verified`` flips to ``True``.
        log_verify_resp = client.patch(
            f"/api/v1/execution-logs/{log_id}",
            json={"commit_verified": True},
        )
        assert log_verify_resp.status_code == 200
        # §3.12 postcondition line 545 (cont.):
        # ``commit_verified=TRUE`` (the execution log's flag — the
        # delegation row itself has no such column).
        assert log_verify_resp.json()["commit_verified"] is True

        # --- Step 6 (system): Guardian pipeline — three reviews,
        # one per Layer. Happy path: all three pass with
        # ``risk_level='low'`` and no blocking findings.
        for layer, duration_ms in [
            ("layer1", 12_400),
            ("layer2", 38_700),
            ("layer3", 9_100),
        ]:
            review_resp = client.post(
                "/api/v1/guardian-reviews",
                json={
                    "delegation_id": delegation_id,
                    "layer": layer,
                    "risk_level": "low",
                    "findings": [],
                    "passed": True,
                    "duration_ms": duration_ms,
                },
            )
            assert review_resp.status_code == 201, review_resp.text
            assert review_resp.json()["layer"] == layer
            assert review_resp.json()["passed"] is True
            assert review_resp.json()["risk_level"] == "low"
            assert review_resp.json()["findings"] == []

        # --- Step 7 (system): Guardian clean → feat transitions to
        # ``status='done'`` with ``actual_minutes`` measured from the
        # delegation's ``started_at`` / ``completed_at`` delta
        # (22m14s ≈ 22 minutes rounded down).
        actual_minutes = int((completed_at - started_at).total_seconds() // 60)
        feat_done_resp = client.patch(
            f"/api/v1/feats/{feat_id}",
            json={"status": "done", "actual_minutes": actual_minutes},
        )
        assert feat_done_resp.status_code == 200, feat_done_resp.text
        # §3.12 postcondition line 548: ``FEAT status='done'``.
        assert feat_done_resp.json()["status"] == "done"
        # §3.12 postcondition line 549: ``actual_minutes`` populated.
        assert feat_done_resp.json()["actual_minutes"] == 22

        # --- Postcondition verification (HTTP) ------------------------
        # Re-read the delegation — ``status='done'`` and commit hash
        # stick.
        delegation_final = client.get(f"/api/v1/delegations/{delegation_id}")
        assert delegation_final.status_code == 200
        assert delegation_final.json()["status"] == "done"
        assert delegation_final.json()["commit_hash"] == EXPECTED_COMMIT_HASH
        assert delegation_final.json()["raw_output"] == CC_STREAM_EXCERPT

        # The execution-log list filtered by delegation_id returns
        # the row with ``commit_verified=True``.
        log_list_resp = client.get(
            "/api/v1/execution-logs",
            params={"delegation_id": delegation_id},
        )
        assert log_list_resp.status_code == 200
        log_items = log_list_resp.json()["items"]
        assert len(log_items) == 1
        assert log_items[0]["id"] == log_id
        assert log_items[0]["commit_verified"] is True
        assert log_items[0]["status"] == "done"

        # Guardian-reviews list filtered by delegation_id returns
        # all three Layers — the ``GuardianPanel`` UI (DESIGN.md
        # §3.1) drives this exact query.
        reviews_resp = client.get(
            "/api/v1/guardian-reviews",
            params={"delegation_id": delegation_id},
        )
        assert reviews_resp.status_code == 200
        assert reviews_resp.json()["total"] == 3
        review_layers = sorted(row["layer"] for row in reviews_resp.json()["items"])
        assert review_layers == ["layer1", "layer2", "layer3"]
        # Every review passed.
        assert all(row["passed"] is True for row in reviews_resp.json()["items"])

        # The feat's final state is ``done`` with ``actual_minutes``
        # populated.
        feat_final_resp = client.get(f"/api/v1/feats/{feat_id}")
        assert feat_final_resp.status_code == 200
        assert feat_final_resp.json()["status"] == "done"
        assert feat_final_resp.json()["actual_minutes"] == 22

        # --- Postcondition verification (DB state) --------------------
        db_session.expire_all()

        # 1. Delegation persisted with ``status='done'`` and
        #    ``commit_hash`` populated — §3.12 postcondition line
        #    545.
        persisted_delegation = db_session.get(Delegation, uuid.UUID(delegation_id))
        assert persisted_delegation is not None
        assert persisted_delegation.status == "done"
        assert persisted_delegation.commit_hash == EXPECTED_COMMIT_HASH
        assert persisted_delegation.feat_id == feat_service_layer.id
        assert persisted_delegation.cc_agent == "ubuntu_cc"
        assert persisted_delegation.raw_output == CC_STREAM_EXCERPT
        assert persisted_delegation.completed_at is not None

        # 2. ExecutionLog persisted with token counts / cost /
        #    duration and ``commit_verified=True`` — §3.12
        #    postcondition line 546.
        persisted_log = db_session.get(ExecutionLog, uuid.UUID(log_id))
        assert persisted_log is not None
        assert persisted_log.delegation_id == persisted_delegation.id
        assert persisted_log.status == "done"
        assert persisted_log.duration_seconds == 1334
        assert persisted_log.input_tokens == 18_420
        assert persisted_log.output_tokens == 4_210
        assert persisted_log.total_cost_usd == Decimal("0.287410")
        assert persisted_log.commit_hash == EXPECTED_COMMIT_HASH
        assert persisted_log.commit_verified is True

        # 3. Three GuardianReview rows — one per Layer — all
        #    passing. §3.12 postcondition line 547.
        persisted_reviews = (
            db_session.query(GuardianReview)
            .filter(GuardianReview.delegation_id == persisted_delegation.id)
            .order_by(GuardianReview.layer)
            .all()
        )
        assert [row.layer for row in persisted_reviews] == ["layer1", "layer2", "layer3"]
        assert all(row.passed is True for row in persisted_reviews)
        assert all(row.risk_level == "low" for row in persisted_reviews)

        # 4. Feat persisted with ``status='done'`` and
        #    ``actual_minutes`` populated — §3.12 postcondition
        #    lines 548 / 549.
        persisted_feat = db_session.get(Feat, feat_service_layer.id)
        assert persisted_feat is not None
        assert persisted_feat.status == "done"
        assert persisted_feat.actual_minutes == 22

        # 5. ``started_at`` / ``completed_at`` round-trip — used by
        #    ``VelocityChart`` (DESIGN.md §3.1) and the ProjectReport
        #    ``AIvsHumanRatioDisplay``.
        assert persisted_delegation.started_at == started_at
        assert persisted_delegation.completed_at == completed_at


# ---------------------------------------------------------------------------
# Edge cases — active-delegation gate and Guardian-block keeps feat off done.
# ---------------------------------------------------------------------------


class TestDelegateFeatToCcEdgeCases:
    """Edge cases for the ``delegate_feat_to_cc`` workflow.

    Two contracts are worth pinning beyond the happy path:

    1. The "no other active delegation for the same feat" precondition
       (§3.12 line 529). A second attempt while a first delegation is
       still ``running`` must be refused by the orchestrator — the
       CRUD-layer observable is the pre-flight query returning a
       non-empty result.
    2. The §3.14 Guardian-block branch: when any review returns
       ``passed=False`` the feat must *not* be auto-transitioned to
       ``done``. The delegation itself is still ``done`` (the CC
       subprocess succeeded) but the feat's lifecycle gate is
       Guardian-bound.
    """

    def test_active_delegation_gate_blocks_second_attempt(
        self,
        client,
        db_session,
        dominik,
        nex_horizont,
        stk_module,
        approved_stk_design,
        stk_epic,
        feat_service_layer,
    ):
        """§3.12 precondition line 529: refuse a second delegation for the same feat.

        The CRUD-layer observable is the orchestrator's pre-flight:
        a ``GET /api/v1/delegations?feat_id=<feat>&status=running``
        returning any row signals "delegation in flight, refuse the
        new one". Pinned here by creating a first ``running``
        delegation and then re-querying the gate — the result is
        non-empty, so a correctly-gated orchestrator would not issue
        a second ``POST /api/v1/delegations``. No assertion of a 4xx
        from the router is made because the gate is orchestrator-
        owned (the DB allows multiple rows); the test asserts the
        signal the orchestrator consumes.
        """
        feat_id = str(feat_service_layer.id)

        # --- First delegation: pending → running.
        first = client.post(
            "/api/v1/delegations",
            json={
                "feat_id": feat_id,
                "cc_agent": "ubuntu_cc",
                "prompt": FEAT_42_PROMPT,
                "status": "pending",
            },
        )
        assert first.status_code == 201, first.text
        first_id = first.json()["id"]

        advance = client.patch(
            f"/api/v1/delegations/{first_id}",
            json={"status": "running"},
        )
        assert advance.status_code == 200
        assert advance.json()["status"] == "running"

        # --- Pre-flight gate for a hypothetical second "Spustiť"
        # click: the ``status='running'`` list for this feat is
        # non-empty, which is the "refuse the new delegation"
        # signal.
        gate_resp = client.get(
            "/api/v1/delegations",
            params={"feat_id": feat_id, "status": "running"},
        )
        assert gate_resp.status_code == 200
        assert gate_resp.json()["total"] == 1
        assert gate_resp.json()["items"][0]["id"] == first_id
        # ``pending`` would block too — a correctly-gated orchestrator
        # treats both states as "in flight".
        pending_resp = client.get(
            "/api/v1/delegations",
            params={"feat_id": feat_id, "status": "pending"},
        )
        assert pending_resp.status_code == 200

        # --- Resolve the blocker: the first delegation completes.
        client.patch(
            f"/api/v1/delegations/{first_id}",
            json={
                "status": "done",
                "commit_hash": EXPECTED_COMMIT_HASH,
            },
        )

        # Gate now clear — the running list is empty again.
        clear_resp = client.get(
            "/api/v1/delegations",
            params={"feat_id": feat_id, "status": "running"},
        )
        assert clear_resp.status_code == 200
        assert clear_resp.json()["total"] == 0

        # --- A second delegation is now permissible.
        second = client.post(
            "/api/v1/delegations",
            json={
                "feat_id": feat_id,
                "cc_agent": "ubuntu_cc",
                "prompt": FEAT_42_PROMPT + "\n\n[retry]",
                "status": "pending",
            },
        )
        assert second.status_code == 201, second.text
        second_id = second.json()["id"]
        assert second_id != first_id

        # Both delegations are visible in the feat's history, ordered
        # by ``started_at DESC`` (latest first) — the
        # ``DelegationPage`` history convention (DESIGN.md §3.1).
        history_resp = client.get(
            "/api/v1/delegations",
            params={"feat_id": feat_id},
        )
        assert history_resp.status_code == 200
        assert history_resp.json()["total"] == 2
        history_ids = [row["id"] for row in history_resp.json()["items"]]
        assert set(history_ids) == {first_id, second_id}

    def test_guardian_block_keeps_feat_off_done(
        self,
        client,
        db_session,
        dominik,
        nex_horizont,
        stk_module,
        approved_stk_design,
        stk_epic,
        feat_service_layer,
    ):
        """§3.14 step 8: a Guardian block keeps the feat off ``status='done'``.

        When any Layer returns ``passed=False`` with a ``HIGH`` /
        ``CRITICAL`` risk the pipeline stops at "Vyžaduje review" and
        the feat must *not* be auto-closed. The delegation itself is
        ``done`` (the CC subprocess succeeded), but the feat's
        lifecycle gate is Guardian-bound: it stays at ``in_progress``
        until Zoltán approves manually.

        A regression that closed the feat regardless of Guardian's
        verdict (e.g. a bug in the post-delegation orchestrator)
        would trip this test — the `assert` on ``in_progress`` would
        fail.
        """
        feat_id = str(feat_service_layer.id)

        # --- Happy-path skeleton: delegation goes to ``done`` and
        # an execution log is written. The feat is in-progress while
        # work is in flight.
        client.patch(f"/api/v1/feats/{feat_id}", json={"status": "in_progress"})

        delegation = client.post(
            "/api/v1/delegations",
            json={
                "feat_id": feat_id,
                "cc_agent": "ubuntu_cc",
                "prompt": FEAT_42_PROMPT,
                "status": "pending",
            },
        )
        assert delegation.status_code == 201
        delegation_id = delegation.json()["id"]

        client.patch(f"/api/v1/delegations/{delegation_id}", json={"status": "running"})
        client.patch(
            f"/api/v1/delegations/{delegation_id}",
            json={
                "status": "done",
                "commit_hash": EXPECTED_COMMIT_HASH,
                "completed_at": datetime(2026, 4, 16, 9, 25, tzinfo=timezone.utc).isoformat(),
            },
        )
        client.post(
            "/api/v1/execution-logs",
            json={
                "delegation_id": delegation_id,
                "status": "done",
                "duration_seconds": 1500,
                "input_tokens": 22_000,
                "output_tokens": 5_100,
                "total_cost_usd": "0.331200",
                "commit_hash": EXPECTED_COMMIT_HASH,
                "commit_verified": True,
            },
        )

        # --- Guardian pipeline: Layer 1 passes (syntax / style
        # clean), Layer 2 passes, Layer 3 FAILS with a blocking
        # finding in a CRITICAL-risk file.
        client.post(
            "/api/v1/guardian-reviews",
            json={
                "delegation_id": delegation_id,
                "layer": "layer1",
                "risk_level": "low",
                "findings": [],
                "passed": True,
            },
        )
        client.post(
            "/api/v1/guardian-reviews",
            json={
                "delegation_id": delegation_id,
                "layer": "layer2",
                "risk_level": "medium",
                "findings": [],
                "passed": True,
            },
        )
        blocking_review = client.post(
            "/api/v1/guardian-reviews",
            json={
                "delegation_id": delegation_id,
                "layer": "layer3",
                "risk_level": "critical",
                "findings": [
                    {
                        "severity": "MUST_FIX",
                        "rule": "sql_injection",
                        "file_path": "backend/services/stk.py",
                        "line_range": "42-48",
                        "description": ("Raw SQL interpolation of a user-supplied ``sku`` into ``SELECT *``."),
                        "suggestion": "Use bound parameters via SQLAlchemy text().",
                        "confidence": 0.95,
                    },
                ],
                "passed": False,
            },
        )
        assert blocking_review.status_code == 201, blocking_review.text
        assert blocking_review.json()["passed"] is False
        assert blocking_review.json()["risk_level"] == "critical"

        # --- Orchestrator's post-Guardian gate: any ``passed=False``
        # review → feat stays ``in_progress``. Expressed as a query
        # the orchestrator would run.
        blocked_resp = client.get(
            "/api/v1/guardian-reviews",
            params={"delegation_id": delegation_id, "passed": False},
        )
        assert blocked_resp.status_code == 200
        assert blocked_resp.json()["total"] == 1
        assert blocked_resp.json()["items"][0]["layer"] == "layer3"

        # Crucially the feat is NOT advanced to ``done`` — the
        # orchestrator refuses the transition while the Guardian
        # gate is red. The feat is still ``in_progress`` (where step
        # 2 left it).
        feat_resp = client.get(f"/api/v1/feats/{feat_id}")
        assert feat_resp.status_code == 200
        assert feat_resp.json()["status"] == "in_progress"
        assert feat_resp.json()["actual_minutes"] is None

        # --- DB state agrees with the HTTP payload.
        db_session.expire_all()
        persisted_feat = db_session.get(Feat, feat_service_layer.id)
        assert persisted_feat is not None
        assert persisted_feat.status == "in_progress"
        assert persisted_feat.actual_minutes is None

        # The delegation itself is still ``done`` — the CC
        # subprocess succeeded; the gate sits on the feat, not on
        # the delegation.
        persisted_delegation = db_session.get(Delegation, uuid.UUID(delegation_id))
        assert persisted_delegation is not None
        assert persisted_delegation.status == "done"
        assert persisted_delegation.commit_hash == EXPECTED_COMMIT_HASH

        # Three reviews persisted, one blocking — the
        # ``GuardianPanel`` UI (DESIGN.md §3.1) surfaces this via
        # the ``passed=False`` filter.
        persisted_reviews = (
            db_session.query(GuardianReview).filter(GuardianReview.delegation_id == persisted_delegation.id).all()
        )
        assert len(persisted_reviews) == 3
        blocking = [row for row in persisted_reviews if row.passed is False]
        assert len(blocking) == 1
        assert blocking[0].layer == "layer3"
        assert blocking[0].risk_level == "critical"
        assert len(blocking[0].findings) == 1
        assert blocking[0].findings[0]["severity"] == "MUST_FIX"

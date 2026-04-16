"""Integration test for BEHAVIOR.md §3.17 ``workflow:delegate_bug_fix``.

Exercises the full happy path of the **delegate_bug_fix** workflow
end-to-end through the real FastAPI ``app``. §3.17 is the
"ship the fix" loop of the bug lifecycle: after Zoltán accepted
BUG-012 via §3.16 and attached two :class:`BugFixTask` rows
(FIX-1 "Fix phone field null handling in PAB service" — backend,
FIX-2 "Add empty phone test" — test), Dominik (``ha_medior``)
picks up the first fix task, clicks "Delegovať", the
orchestrator fires a CC subprocess that lands a commit, and the
task transitions to ``status='done'``. The second fix task is
delegated the same way. Once every fix task on the bug is
``done`` the orchestrator advances the bug itself from
``status='accepted'`` to ``status='in_progress'``.

The worked example throughout is drawn from BEHAVIOR.md §3.17
step 1 verbatim: "Dominik otvorí BUG-012 → FIX-1 → klikne
'Delegovať'". The delegation row that results carries both a
``bug_fix_task_id`` (pointing at FIX-1) and a ``bug_id`` (pointing
at BUG-012) — the §3.17 step 1 system response is explicit that
both columns are populated so the bugs-page "delegation history"
query (``GET /api/v1/delegations?bug_id=...``) and the
fix-task-detail "my delegations" query
(``GET /api/v1/delegations?bug_fix_task_id=...``) both return
the row.

The CC subprocess itself, the SSE / WebSocket stream, the
GitHub API commit-verification call and the auto-advance of the
bug to ``status='in_progress'`` once every fix task is ``done``
are all orchestration concerns and out of scope at the HTTP /
CRUD layer. The test supplies the structured side effects those
orchestration components would produce (the ``commit_hash`` CC
extracts from its output, the ``status='done'`` PATCH the
orchestrator applies to the fix task after a successful run, and
the ``status='in_progress'`` PATCH it applies to the bug once
the "all fix tasks done" signal fires) and verifies the
*observable* side effects against the HTTP contract and the DB
state.

    Precondition (per BEHAVIOR.md §3.17, lines 685-688):
        * :class:`BugFixTask` rows exist with ``status='todo'`` —
          the §3.16 postcondition. Both FIX-1 and FIX-2 are
          seeded directly at ``status='todo'`` so §3.17 has
          something to delegate.
        * Parent :class:`Bug` has ``status='accepted'`` or
          ``status='in_progress'`` — the §3.16 postcondition
          (``accepted`` after §3.16 step 1, ``in_progress`` after
          at least one §3.17 cycle completes). BUG-012 is seeded
          at ``accepted``; the happy-path flow then drives it to
          ``in_progress`` on the "all fix tasks done" signal (step
          5 system response).
        * Actor is a member of the project — satisfied by
          persisting a :class:`ProjectMember` row for Dominik on
          NEX Horizont.

    Steps (per BEHAVIOR.md §3.17, lines 692-698):
        1. Dominik opens BUG-012 → FIX-1 → clicks "Delegovať" →
           the orchestrator creates a :class:`Delegation` row
           with ``bug_fix_task_id=fix1_id`` *and* ``bug_id=bug012_id``
           both populated (the two are siblings on the
           :mod:`backend.db.models.delegations.Delegation` table,
           each with ``ON DELETE SET NULL``; the §3.17 step 1
           system response names both explicitly). ``status``
           starts ``pending``.
        2. — (system) — CC subprocess starts, streaming output
           into ``raw_output`` via PATCH. Modelled here as a
           ``status`` transition to ``running`` plus a single
           ``raw_output`` PATCH to pin the "captured stream"
           contract.
        3. CC exits successfully — the orchestrator PATCHes the
           delegation to ``status='done'`` with ``commit_hash``
           populated, sets the fix task to ``status='done'`` and
           logs an :class:`ExecutionLog` with ``commit_verified``
           flipped to ``True`` once the GitHub API call lands.
        4. Dominik delegates FIX-2 (test) — the same three
           sub-steps are walked for the second fix task, producing
           a second :class:`Delegation` row under BUG-012.
        5. — (system) — once every fix task under BUG-012 is
           ``done`` the orchestrator PATCHes the bug itself to
           ``status='in_progress'`` (the postcondition). The
           observable signal is the list query
           ``GET /api/v1/bug-fix-tasks?bug_id=<bug>&status=todo``
           returning zero rows.

    Postcondition (per BEHAVIOR.md §3.17, lines 700-703):
        * :class:`Delegation` rows exist with ``bug_fix_task_id``
          linking each delegation back to its fix task (and
          ``bug_id`` linking back to the parent bug).
        * Every :class:`BugFixTask` under the bug is
          ``status='done'``.
        * The :class:`Bug` is at ``status='in_progress'``.

Edge cases verified alongside the happy path:

    * **Partial-completion gate** — while any fix task is still
      ``todo`` / ``in_progress`` / ``failed`` the bug must *not*
      auto-transition to ``in_progress``. The orchestrator only
      fires the bug-status PATCH when the ``todo`` / ``in_progress``
      fix-task inbox is empty. Pins the "all fix tasks done"
      precondition from §3.17 step 5 at the HTTP layer, so a
      regression that advanced the bug after the first fix task
      finished would fail this test.
    * **CC failure leaves the fix task undone** — when CC exits
      with a non-zero status the orchestrator PATCHes the
      delegation to ``status='failed'`` and the fix task stays at
      ``in_progress`` (not ``done``). The bug therefore stays at
      ``accepted``. Pins the §3.17 step 3 "CC skončí úspešne" gate:
      only a successful CC run flips the fix task to ``done``.

Auth note:
    Same as the rest of the Feat 7 integration tests — the router
    layer does not wire a JWT dependency yet, so the "Actor is a
    project member" precondition is satisfied by persisting the
    actor with the correct ``role`` and seeding a
    :class:`ProjectMember` row. Role enforcement is a separate
    auth-middleware concern; the test focuses on the CRUD-layer
    contract the frontend hits.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.db.models.bugs import Bug, BugFixTask
from backend.db.models.delegations import Delegation, ExecutionLog
from backend.db.models.foundation import User
from backend.db.models.projects import Project, ProjectMember

# ---------------------------------------------------------------------------
# Precondition fixtures — NEX Horizont project with Zoltán (ri_director),
# Dominik (ha_medior — the §3.17 actor) and Nazar (shu_junior — BUG-012's
# reporter per §3.15) as members. BUG-012 is pre-seeded at
# ``status='accepted'`` with two fix tasks (FIX-1 backend, FIX-2 test) at
# ``status='todo'`` — the §3.16 postcondition. §3.17 picks up from here.
# ---------------------------------------------------------------------------


@pytest.fixture()
def dominik(db_session) -> User:
    """Persist Dominik — the ``ha_medior`` primary actor in §3.17's worked example.

    BEHAVIOR.md §3.17 line 694 names Dominik explicitly ("Dominik
    otvorí BUG-012 → FIX-1 → klikne 'Delegovať'"). Role is ``ha``;
    the §3.17 actor line also names ``ri_director`` and
    ``ri_senior`` as valid, but the worked example is Dominik's.
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
    """Persist Zoltán — the ``ri_director`` who accepted BUG-012 via §3.16.

    Zoltán is not the §3.17 actor but is the named approver who
    moved BUG-012 from ``new`` to ``accepted`` in §3.16. His
    ``User`` row is needed for the project-members table and for
    the ``created_by`` on the seeded project.
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
def nazar(db_session) -> User:
    """Persist Nazar — the ``shu_junior`` who filed BUG-012 via §3.15.

    Nazar is the ``created_by`` for BUG-012 — §3.15 worked
    example. He plays no role in §3.17 itself (the bug is already
    accepted by §3.17's time), but the FK ``bugs.created_by`` is
    NOT NULL so the fixture graph needs him.
    """
    user = User(
        username="nazar",
        email="nazar@isnex.ai",
        password_hash="hashed-placeholder",
        role="shu",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def nex_horizont(db_session, zoltan, dominik, nazar) -> Project:
    """Persist the NEX Horizont project with Dominik, Zoltán and Nazar as members.

    §3.17 precondition line 688: "Actor je člen projektu". Dominik
    is the §3.17 actor and therefore needs a :class:`ProjectMember`
    row. Zoltán and Nazar are kept as members so the fixture graph
    also backs §3.16 (Zoltán accepting the bug) and §3.15 (Nazar
    filing it).
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

    for user in (zoltan, dominik, nazar):
        db_session.add(ProjectMember(project_id=project.id, user_id=user.id))
    db_session.flush()
    return project


@pytest.fixture()
def bug_012(db_session, nex_horizont, nazar) -> Bug:
    """Seed BUG-012 at ``status='accepted'`` — the §3.17 precondition.

    BEHAVIOR.md §3.17 precondition line 687: "Rodičovský bug má
    ``status='accepted'`` alebo ``status='in_progress'``". The
    §3.16 postcondition is ``accepted``; we seed directly at that
    state so §3.17 has an accepted bug to delegate fixes for.

    ``bug_number=12`` reproduces the "BUG-012" label from §3.17
    step 1 faithfully. ``created_by=nazar.id`` mirrors §3.15 step
    3 — Nazar is the reporter.
    """
    bug = Bug(
        project_id=nex_horizont.id,
        bug_number=12,
        title="PAB detail page crashes on empty phone field",
        description=(
            "Reprodukcia:\n"
            "1. Otvor PAB (Katalóg partnerov) → detail partnera.\n"
            "2. Vymaž hodnotu poľa 'Telefón' a klikni 'Uložiť'.\n"
            "3. Stránka havaruje s TypeError: Cannot read properties of null.\n"
        ),
        severity="major",
        status="accepted",
        source="internal",
        environment="development",
        created_by=nazar.id,
    )
    db_session.add(bug)
    db_session.flush()
    return bug


# The §3.16 worked example attached two fix tasks to BUG-012.  The
# titles / types below reproduce BEHAVIOR.md §3.16 step 3 verbatim.
FIX_1_TITLE = "Fix phone field null handling in PAB service"
FIX_1_DESCRIPTION = (
    "Update PAB service layer to treat ``phone=NULL`` as a valid empty "
    "value: accept NULL on UPDATE, render as blank in the detail view, "
    "and add defensive guard in the serializer."
)
FIX_1_TASK_TYPE = "backend"

FIX_2_TITLE = "Add empty phone test"
FIX_2_DESCRIPTION = "Regression test pinning the empty phone field accepts NULL and the detail page renders."
FIX_2_TASK_TYPE = "test"


@pytest.fixture()
def fix_1(db_session, bug_012) -> BugFixTask:
    """Seed BUG-012/FIX-1 at ``status='todo'`` — the §3.17 precondition.

    §3.17 precondition line 686 requires ``bug_fix_tasks`` with
    ``status='todo'``. FIX-1 mirrors the §3.16 step 3 backend fix
    task exactly (``number=1``, ``task_type='backend'``).
    """
    task = BugFixTask(
        bug_id=bug_012.id,
        number=1,
        title=FIX_1_TITLE,
        description=FIX_1_DESCRIPTION,
        task_type=FIX_1_TASK_TYPE,
        status="todo",
        checklist_type="service",
    )
    db_session.add(task)
    db_session.flush()
    return task


@pytest.fixture()
def fix_2(db_session, bug_012) -> BugFixTask:
    """Seed BUG-012/FIX-2 at ``status='todo'`` — the §3.17 step 4 target.

    §3.17 step 4 "Dominik deleguje FIX-2 (test) — Rovnaký proces"
    picks up the second fix task. Seeded at ``number=2`` to
    preserve the "BUG-012/FIX-2" label that ``MAX(number) + 1``
    would have produced under §3.16's worked example.
    """
    task = BugFixTask(
        bug_id=bug_012.id,
        number=2,
        title=FIX_2_TITLE,
        description=FIX_2_DESCRIPTION,
        task_type=FIX_2_TASK_TYPE,
        status="todo",
        checklist_type="test",
    )
    db_session.add(task)
    db_session.flush()
    return task


# ---------------------------------------------------------------------------
# Helpers — representative prompt + commit hash + stream excerpt for the
# orchestrator's side of the workflow.
# ---------------------------------------------------------------------------


def _fix_prompt(fix_task: BugFixTask, bug: Bug) -> str:
    """Build a representative CC prompt for a bug fix task.

    The exact prompt format is orchestration territory. The
    delegation table requires ``prompt NOT NULL`` so the test
    supplies a non-empty string that names the fix task and bug
    being worked on, mirroring the structure used by the §3.12
    feat-delegation test's ``FEAT_42_PROMPT`` constant.
    """
    return (
        f"You are implementing BUG-{bug.bug_number}/FIX-{fix_task.number} "
        f"'{fix_task.title}' in the NEX Horizont project. Context:\n\n"
        "## Bug\n"
        f"{bug.title}\n\n"
        f"{bug.description}\n\n"
        "## Fix task\n"
        f"{fix_task.description}\n\n"
        "## Checklist\n"
        f"- {fix_task.checklist_type}\n"
    )


# Representative commit hashes CC would extract from its output
# (DESIGN.md §1.7 ``commit_hash`` is a 40-char SHA-1 hex).
FIX_1_COMMIT_HASH = "b4c2d7e6f1a8c3b5e4d7a9f6c8b3d5e1a2f4c9d8"
FIX_2_COMMIT_HASH = "c5d3e8f7a2b9d4c6f5e8b0a7d9c4e6f2b3a5dae9"

# Short NDJSON-ish excerpts — not parsed at the CRUD layer, just
# stored verbatim on ``delegations.raw_output`` so the "captured
# stream" contract is exercised.
FIX_1_STREAM_EXCERPT = (
    '{"type":"tool_use","name":"Read","input":{"file_path":"backend/services/pab.py"}}\n'
    '{"type":"tool_use","name":"Edit","input":{"file_path":"backend/services/pab.py"}}\n'
    '{"type":"tool_use","name":"Bash","input":{"command":"poetry run pytest tests/test_pab_service.py"}}\n'
    f'{{"type":"result","commit_hash":"{FIX_1_COMMIT_HASH}"}}\n'
)
FIX_2_STREAM_EXCERPT = (
    '{"type":"tool_use","name":"Write","input":{"file_path":"tests/test_pab_empty_phone.py"}}\n'
    '{"type":"tool_use","name":"Bash","input":{"command":"poetry run pytest tests/test_pab_empty_phone.py"}}\n'
    f'{{"type":"result","commit_hash":"{FIX_2_COMMIT_HASH}"}}\n'
)


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.17 end-to-end, BUG-012 + FIX-1 + FIX-2.
# ---------------------------------------------------------------------------


class TestDelegateBugFixHappyPath:
    """End-to-end walkthrough of workflow §3.17 against the real app."""

    def test_full_workflow_delegates_both_fixes_and_advances_bug(
        self,
        client,
        db_session,
        dominik,
        nex_horizont,
        bug_012,
        fix_1,
        fix_2,
    ):
        """Drive steps 1-5 of §3.17 and verify every postcondition.

        Reproduces the §3.17 worked example faithfully: Dominik
        delegates BUG-012/FIX-1 (backend), the CC subprocess ships
        a commit and the fix task lands at ``status='done'``;
        Dominik then delegates BUG-012/FIX-2 (test) the same way.
        Once both fix tasks are ``done`` the orchestrator advances
        BUG-012 itself from ``accepted`` to ``in_progress`` — the
        §3.17 step 5 system response and postcondition.
        """
        bug_id = str(bug_012.id)
        fix_1_id = str(fix_1.id)
        fix_2_id = str(fix_2.id)

        # --- Pre-flight (precondition recap): BUG-012 is ``accepted``
        # and has two ``todo`` fix tasks — §3.17 precondition lines
        # 686-687. This is the "Dominik opens BUG-012" read.
        bug_resp = client.get(f"/api/v1/bugs/{bug_id}")
        assert bug_resp.status_code == 200, bug_resp.text
        assert bug_resp.json()["status"] == "accepted"
        assert bug_resp.json()["bug_number"] == 12

        todo_inbox = client.get(
            "/api/v1/bug-fix-tasks",
            params={"bug_id": bug_id, "status": "todo"},
        )
        assert todo_inbox.status_code == 200
        assert todo_inbox.json()["total"] == 2
        todo_ids = {row["id"] for row in todo_inbox.json()["items"]}
        assert todo_ids == {fix_1_id, fix_2_id}

        # Pre-flight: no other active delegation for this fix task.
        no_active_fix_1 = client.get(
            "/api/v1/delegations",
            params={"bug_fix_task_id": fix_1_id, "status": "running"},
        )
        assert no_active_fix_1.status_code == 200
        assert no_active_fix_1.json()["total"] == 0

        # ====================================================================
        # STEP 1 — Dominik opens FIX-1 → clicks "Delegovať". Orchestrator
        # creates a delegation with both ``bug_fix_task_id`` and ``bug_id``
        # populated (the §3.17 step 1 system response names both explicitly).
        # ====================================================================
        fix_1_started_at = datetime(2026, 4, 16, 9, 0, 0, tzinfo=timezone.utc)
        delegation_1_create = client.post(
            "/api/v1/delegations",
            json={
                "bug_fix_task_id": fix_1_id,
                "bug_id": bug_id,
                "cc_agent": "ubuntu_cc",
                "prompt": _fix_prompt(fix_1, bug_012),
                "status": "pending",
                "started_at": fix_1_started_at.isoformat(),
            },
        )
        assert delegation_1_create.status_code == 201, delegation_1_create.text
        delegation_1_body = delegation_1_create.json()
        delegation_1_id = delegation_1_body["id"]
        # §3.17 step 1 system response: ``bug_fix_task_id=fix1_id`` AND
        # ``bug_id=bug012_id`` — both columns are populated, not one or the
        # other. This is the contract the bugs-page "delegation history"
        # query and the fix-task-detail "my delegations" query both rely
        # on.
        assert delegation_1_body["bug_fix_task_id"] == fix_1_id
        assert delegation_1_body["bug_id"] == bug_id
        assert delegation_1_body["feat_id"] is None
        assert delegation_1_body["task_id"] is None
        assert delegation_1_body["cc_agent"] == "ubuntu_cc"
        assert delegation_1_body["status"] == "pending"
        # Lifecycle fields are pristine — the CC subprocess has not
        # started yet.
        assert delegation_1_body["raw_output"] is None
        assert delegation_1_body["commit_hash"] is None
        assert delegation_1_body["completed_at"] is None

        # The orchestrator flips the fix task to ``in_progress`` so the
        # fix-task-detail card shows "work in flight" — mirrors the
        # §3.12 feat-level delegation's ``in_progress`` transition.
        fix_1_in_progress = client.patch(
            f"/api/v1/bug-fix-tasks/{fix_1_id}",
            json={"status": "in_progress"},
        )
        assert fix_1_in_progress.status_code == 200
        assert fix_1_in_progress.json()["status"] == "in_progress"

        # ====================================================================
        # STEP 2 — (system) — CC subprocess starts. Delegation → ``running``.
        # Dominik watches streaming output accumulate into ``raw_output``.
        # ====================================================================
        running_1 = client.patch(
            f"/api/v1/delegations/{delegation_1_id}",
            json={"status": "running"},
        )
        assert running_1.status_code == 200
        assert running_1.json()["status"] == "running"

        stream_1 = client.patch(
            f"/api/v1/delegations/{delegation_1_id}",
            json={"raw_output": FIX_1_STREAM_EXCERPT},
        )
        assert stream_1.status_code == 200
        assert stream_1.json()["raw_output"] == FIX_1_STREAM_EXCERPT
        # Streaming PATCH does not terminate the delegation.
        assert stream_1.json()["status"] == "running"

        # ====================================================================
        # STEP 3 — CC exits 0. Orchestrator PATCHes the delegation to
        # ``done`` with ``commit_hash`` populated, PATCHes the fix task to
        # ``done``, and logs an :class:`ExecutionLog` with the usual token
        # / cost / duration fields. GitHub API then flips
        # ``commit_verified`` to ``True``.
        # ====================================================================
        fix_1_completed_at = fix_1_started_at + timedelta(minutes=9, seconds=42)
        done_1 = client.patch(
            f"/api/v1/delegations/{delegation_1_id}",
            json={
                "status": "done",
                "commit_hash": FIX_1_COMMIT_HASH,
                "completed_at": fix_1_completed_at.isoformat(),
            },
        )
        assert done_1.status_code == 200, done_1.text
        done_1_body = done_1.json()
        assert done_1_body["status"] == "done"
        assert done_1_body["commit_hash"] == FIX_1_COMMIT_HASH
        assert done_1_body["completed_at"] is not None

        # Fix task advances to ``status='done'`` — §3.17 step 3
        # system response: "Systém nastaví ``bug_fix_task.status='done'``,
        # verifikuje commit".
        fix_1_actual_minutes = int((fix_1_completed_at - fix_1_started_at).total_seconds() // 60)
        fix_1_done = client.patch(
            f"/api/v1/bug-fix-tasks/{fix_1_id}",
            json={"status": "done", "actual_minutes": fix_1_actual_minutes},
        )
        assert fix_1_done.status_code == 200
        assert fix_1_done.json()["status"] == "done"
        assert fix_1_done.json()["actual_minutes"] == 9

        # The execution log captures token counts / cost / commit.
        log_1_create = client.post(
            "/api/v1/execution-logs",
            json={
                "delegation_id": delegation_1_id,
                "status": "done",
                "duration_seconds": 582,
                "input_tokens": 8_120,
                "output_tokens": 2_040,
                "total_cost_usd": "0.117340",
                "commit_hash": FIX_1_COMMIT_HASH,
                "commit_verified": False,
            },
        )
        assert log_1_create.status_code == 201, log_1_create.text
        log_1_id = log_1_create.json()["id"]

        # §3.17 step 3 "verifikuje commit" — GitHub API says the commit
        # landed on ``main``, so ``commit_verified`` flips to ``True``.
        verify_1 = client.patch(
            f"/api/v1/execution-logs/{log_1_id}",
            json={"commit_verified": True},
        )
        assert verify_1.status_code == 200
        assert verify_1.json()["commit_verified"] is True

        # ====================================================================
        # INTERMEDIATE STATE CHECK — FIX-1 is ``done``, FIX-2 is still
        # ``todo``. The bug MUST still be at ``accepted`` — the §3.17
        # step 5 "all fix tasks done" gate has not fired yet. This is the
        # partial-completion gate; it's pinned as a dedicated edge-case
        # test too, but asserting it here keeps the happy-path narrative
        # honest (i.e. we don't accidentally depend on "always advance
        # after any fix").
        # ====================================================================
        mid_bug = client.get(f"/api/v1/bugs/{bug_id}")
        assert mid_bug.status_code == 200
        assert mid_bug.json()["status"] == "accepted"
        mid_todo_inbox = client.get(
            "/api/v1/bug-fix-tasks",
            params={"bug_id": bug_id, "status": "todo"},
        )
        assert mid_todo_inbox.status_code == 200
        # Exactly FIX-2 is still ``todo``.
        assert mid_todo_inbox.json()["total"] == 1
        assert mid_todo_inbox.json()["items"][0]["id"] == fix_2_id

        # ====================================================================
        # STEP 4 — Dominik delegates FIX-2 (test). Rovnaký proces:
        # same ``pending → running → done`` lifecycle, a second
        # :class:`Delegation` row under BUG-012.
        # ====================================================================
        fix_2_started_at = fix_1_completed_at + timedelta(minutes=5)
        delegation_2_create = client.post(
            "/api/v1/delegations",
            json={
                "bug_fix_task_id": fix_2_id,
                "bug_id": bug_id,
                "cc_agent": "ubuntu_cc",
                "prompt": _fix_prompt(fix_2, bug_012),
                "status": "pending",
                "started_at": fix_2_started_at.isoformat(),
            },
        )
        assert delegation_2_create.status_code == 201, delegation_2_create.text
        delegation_2_body = delegation_2_create.json()
        delegation_2_id = delegation_2_body["id"]
        # Both pointers populated again — §3.17 step 1 system response
        # applies to every delegation spawned by step 4 too.
        assert delegation_2_body["bug_fix_task_id"] == fix_2_id
        assert delegation_2_body["bug_id"] == bug_id
        # FIX-1 and FIX-2 delegations are distinct rows.
        assert delegation_2_id != delegation_1_id

        # Fix task → ``in_progress``.
        fix_2_in_progress = client.patch(
            f"/api/v1/bug-fix-tasks/{fix_2_id}",
            json={"status": "in_progress"},
        )
        assert fix_2_in_progress.status_code == 200
        assert fix_2_in_progress.json()["status"] == "in_progress"

        # CC running + streaming.
        client.patch(
            f"/api/v1/delegations/{delegation_2_id}",
            json={"status": "running"},
        )
        client.patch(
            f"/api/v1/delegations/{delegation_2_id}",
            json={"raw_output": FIX_2_STREAM_EXCERPT},
        )

        # CC exits 0 — delegation done, fix task done, log written +
        # verified.
        fix_2_completed_at = fix_2_started_at + timedelta(minutes=4, seconds=18)
        done_2 = client.patch(
            f"/api/v1/delegations/{delegation_2_id}",
            json={
                "status": "done",
                "commit_hash": FIX_2_COMMIT_HASH,
                "completed_at": fix_2_completed_at.isoformat(),
            },
        )
        assert done_2.status_code == 200, done_2.text
        assert done_2.json()["status"] == "done"
        assert done_2.json()["commit_hash"] == FIX_2_COMMIT_HASH

        fix_2_actual_minutes = int((fix_2_completed_at - fix_2_started_at).total_seconds() // 60)
        fix_2_done = client.patch(
            f"/api/v1/bug-fix-tasks/{fix_2_id}",
            json={"status": "done", "actual_minutes": fix_2_actual_minutes},
        )
        assert fix_2_done.status_code == 200
        assert fix_2_done.json()["status"] == "done"
        assert fix_2_done.json()["actual_minutes"] == 4

        log_2_create = client.post(
            "/api/v1/execution-logs",
            json={
                "delegation_id": delegation_2_id,
                "status": "done",
                "duration_seconds": 258,
                "input_tokens": 5_210,
                "output_tokens": 1_380,
                "total_cost_usd": "0.064820",
                "commit_hash": FIX_2_COMMIT_HASH,
                "commit_verified": True,
            },
        )
        assert log_2_create.status_code == 201, log_2_create.text
        log_2_id = log_2_create.json()["id"]
        assert log_2_create.json()["commit_verified"] is True

        # ====================================================================
        # STEP 5 — (system) — Every fix task is ``done``. The
        # orchestrator observes the "no todo / in_progress fix tasks"
        # signal and PATCHes the bug itself from ``accepted`` to
        # ``in_progress``. This is the §3.17 step 5 system response and
        # the postcondition on bug status.
        # ====================================================================
        # Pre-flight signal the orchestrator would consume: zero
        # un-done fix tasks under BUG-012.
        final_todo = client.get(
            "/api/v1/bug-fix-tasks",
            params={"bug_id": bug_id, "status": "todo"},
        )
        assert final_todo.status_code == 200
        assert final_todo.json()["total"] == 0
        final_in_progress = client.get(
            "/api/v1/bug-fix-tasks",
            params={"bug_id": bug_id, "status": "in_progress"},
        )
        assert final_in_progress.status_code == 200
        assert final_in_progress.json()["total"] == 0

        # Bug PATCHed to ``in_progress``.
        bug_in_progress = client.patch(
            f"/api/v1/bugs/{bug_id}",
            json={"status": "in_progress"},
        )
        assert bug_in_progress.status_code == 200, bug_in_progress.text
        # §3.17 step 5 system response + postcondition line 703.
        assert bug_in_progress.json()["status"] == "in_progress"
        # The ``resolved_at`` auto-stamp only fires on the ``→ resolved``
        # transition. ``→ in_progress`` must not stamp it.
        assert bug_in_progress.json()["resolved_at"] is None

        # ====================================================================
        # Postcondition verification (HTTP) ------------------------------
        # ====================================================================
        # Postcondition line 701: delegations with ``bug_fix_task_id``
        # linking each delegation back to its fix task. The bugs-page
        # delegation-history query surfaces both rows ordered by
        # ``started_at DESC``.
        bug_delegations = client.get(
            "/api/v1/delegations",
            params={"bug_id": bug_id},
        )
        assert bug_delegations.status_code == 200
        assert bug_delegations.json()["total"] == 2
        # Latest first — FIX-2 started after FIX-1.
        bug_delegation_ids = [row["id"] for row in bug_delegations.json()["items"]]
        assert bug_delegation_ids == [delegation_2_id, delegation_1_id]
        # Every delegation carries both pointers.
        for row in bug_delegations.json()["items"]:
            assert row["bug_id"] == bug_id
            assert row["bug_fix_task_id"] in {fix_1_id, fix_2_id}
            assert row["feat_id"] is None
            assert row["task_id"] is None
            assert row["status"] == "done"
            assert row["commit_hash"] in {FIX_1_COMMIT_HASH, FIX_2_COMMIT_HASH}

        # Per-fix-task lookup returns exactly one delegation each.
        fix_1_delegations = client.get(
            "/api/v1/delegations",
            params={"bug_fix_task_id": fix_1_id},
        )
        assert fix_1_delegations.status_code == 200
        assert fix_1_delegations.json()["total"] == 1
        assert fix_1_delegations.json()["items"][0]["id"] == delegation_1_id
        assert fix_1_delegations.json()["items"][0]["commit_hash"] == FIX_1_COMMIT_HASH

        fix_2_delegations = client.get(
            "/api/v1/delegations",
            params={"bug_fix_task_id": fix_2_id},
        )
        assert fix_2_delegations.status_code == 200
        assert fix_2_delegations.json()["total"] == 1
        assert fix_2_delegations.json()["items"][0]["id"] == delegation_2_id
        assert fix_2_delegations.json()["items"][0]["commit_hash"] == FIX_2_COMMIT_HASH

        # Postcondition line 702: every :class:`BugFixTask` under the
        # bug is ``status='done'``. The ``done`` filter returns both
        # rows; the ``todo`` / ``in_progress`` filter returns none.
        done_tasks = client.get(
            "/api/v1/bug-fix-tasks",
            params={"bug_id": bug_id, "status": "done"},
        )
        assert done_tasks.status_code == 200
        assert done_tasks.json()["total"] == 2
        assert {row["id"] for row in done_tasks.json()["items"]} == {fix_1_id, fix_2_id}

        # Postcondition line 703: bug is ``in_progress``.
        final_bug = client.get(f"/api/v1/bugs/{bug_id}")
        assert final_bug.status_code == 200
        assert final_bug.json()["status"] == "in_progress"
        assert final_bug.json()["resolved_at"] is None
        # And the "accepted" inbox no longer returns BUG-012.
        accepted_inbox = client.get(
            "/api/v1/bugs",
            params={"project_id": str(nex_horizont.id), "status": "accepted"},
        )
        assert accepted_inbox.status_code == 200
        assert accepted_inbox.json()["total"] == 0
        # It's now in the ``in_progress`` inbox.
        in_progress_inbox = client.get(
            "/api/v1/bugs",
            params={"project_id": str(nex_horizont.id), "status": "in_progress"},
        )
        assert in_progress_inbox.status_code == 200
        assert in_progress_inbox.json()["total"] == 1
        assert in_progress_inbox.json()["items"][0]["id"] == bug_id

        # Execution logs for both delegations carry ``commit_verified``
        # — the "commit landed on main" gate from §3.17 step 3.
        log_1_resp = client.get(f"/api/v1/execution-logs/{log_1_id}")
        assert log_1_resp.status_code == 200
        assert log_1_resp.json()["commit_verified"] is True
        assert log_1_resp.json()["commit_hash"] == FIX_1_COMMIT_HASH
        log_2_resp = client.get(f"/api/v1/execution-logs/{log_2_id}")
        assert log_2_resp.status_code == 200
        assert log_2_resp.json()["commit_verified"] is True
        assert log_2_resp.json()["commit_hash"] == FIX_2_COMMIT_HASH

        # ====================================================================
        # Postcondition verification (DB state) --------------------------
        # ====================================================================
        db_session.expire_all()

        # 1. Two delegations persisted, each with both
        #    ``bug_fix_task_id`` and ``bug_id`` populated — §3.17
        #    postcondition line 701.
        persisted_delegation_1 = db_session.get(Delegation, uuid.UUID(delegation_1_id))
        persisted_delegation_2 = db_session.get(Delegation, uuid.UUID(delegation_2_id))
        assert persisted_delegation_1 is not None
        assert persisted_delegation_2 is not None
        assert persisted_delegation_1.bug_fix_task_id == fix_1.id
        assert persisted_delegation_1.bug_id == bug_012.id
        assert persisted_delegation_1.feat_id is None
        assert persisted_delegation_1.task_id is None
        assert persisted_delegation_1.status == "done"
        assert persisted_delegation_1.commit_hash == FIX_1_COMMIT_HASH
        assert persisted_delegation_1.raw_output == FIX_1_STREAM_EXCERPT
        assert persisted_delegation_1.started_at == fix_1_started_at
        assert persisted_delegation_1.completed_at == fix_1_completed_at

        assert persisted_delegation_2.bug_fix_task_id == fix_2.id
        assert persisted_delegation_2.bug_id == bug_012.id
        assert persisted_delegation_2.feat_id is None
        assert persisted_delegation_2.task_id is None
        assert persisted_delegation_2.status == "done"
        assert persisted_delegation_2.commit_hash == FIX_2_COMMIT_HASH
        assert persisted_delegation_2.raw_output == FIX_2_STREAM_EXCERPT
        assert persisted_delegation_2.started_at == fix_2_started_at
        assert persisted_delegation_2.completed_at == fix_2_completed_at

        # 2. Fix tasks persisted at ``status='done'`` — §3.17
        #    postcondition line 702.
        persisted_fix_1 = db_session.get(BugFixTask, fix_1.id)
        persisted_fix_2 = db_session.get(BugFixTask, fix_2.id)
        assert persisted_fix_1 is not None
        assert persisted_fix_2 is not None
        assert persisted_fix_1.status == "done"
        assert persisted_fix_2.status == "done"
        assert persisted_fix_1.number == 1
        assert persisted_fix_2.number == 2
        assert persisted_fix_1.bug_id == bug_012.id
        assert persisted_fix_2.bug_id == bug_012.id
        assert persisted_fix_1.actual_minutes == 9
        assert persisted_fix_2.actual_minutes == 4

        # 3. Bug persisted at ``status='in_progress'`` — §3.17
        #    postcondition line 703. ``resolved_at`` untouched.
        persisted_bug = db_session.get(Bug, bug_012.id)
        assert persisted_bug is not None
        assert persisted_bug.status == "in_progress"
        assert persisted_bug.resolved_at is None
        assert persisted_bug.commit_hash is None  # the resolve workflow stamps this, not §3.17
        assert persisted_bug.bug_number == 12

        # 4. Execution logs persisted with commit / verification flags.
        persisted_log_1 = db_session.get(ExecutionLog, uuid.UUID(log_1_id))
        persisted_log_2 = db_session.get(ExecutionLog, uuid.UUID(log_2_id))
        assert persisted_log_1 is not None
        assert persisted_log_2 is not None
        assert persisted_log_1.delegation_id == persisted_delegation_1.id
        assert persisted_log_2.delegation_id == persisted_delegation_2.id
        assert persisted_log_1.commit_verified is True
        assert persisted_log_2.commit_verified is True
        assert persisted_log_1.total_cost_usd == Decimal("0.117340")
        assert persisted_log_2.total_cost_usd == Decimal("0.064820")


# ---------------------------------------------------------------------------
# Edge cases — partial-completion gate and CC-failure path.
# ---------------------------------------------------------------------------


class TestDelegateBugFixEdgeCases:
    """Edge cases for the ``delegate_bug_fix`` workflow.

    Two contracts are worth pinning beyond the happy path:

    1. The §3.17 step 5 "all fix tasks done" gate — while any fix
       task is still ``todo`` / ``in_progress`` / ``failed`` the
       bug must *not* auto-transition to ``in_progress``. The
       orchestrator only fires the bug-status PATCH when the
       un-done fix-task inbox is empty.
    2. The §3.17 step 3 "CC skončí úspešne" gate — only a
       successful CC run flips the fix task to ``done``. A failed
       CC run leaves the delegation at ``status='failed'``, the
       fix task stays at ``in_progress``, and the bug stays at
       ``accepted``.
    """

    def test_partial_completion_keeps_bug_at_accepted(
        self,
        client,
        db_session,
        dominik,
        nex_horizont,
        bug_012,
        fix_1,
        fix_2,
    ):
        """§3.17 step 5 gate: bug stays ``accepted`` while any fix is un-done.

        Dominik delegates FIX-1 and it completes successfully. But
        FIX-2 is still ``todo`` — so the "all fix tasks done"
        signal the orchestrator consumes to fire the bug PATCH
        from ``accepted`` → ``in_progress`` has not fired yet. A
        regression that advanced the bug after the *first* fix
        task finished would fail this test.
        """
        bug_id = str(bug_012.id)
        fix_1_id = str(fix_1.id)
        fix_2_id = str(fix_2.id)

        # --- Delegate FIX-1 and walk it to ``done``.
        delegation = client.post(
            "/api/v1/delegations",
            json={
                "bug_fix_task_id": fix_1_id,
                "bug_id": bug_id,
                "cc_agent": "ubuntu_cc",
                "prompt": _fix_prompt(fix_1, bug_012),
                "status": "pending",
            },
        )
        assert delegation.status_code == 201
        delegation_id = delegation.json()["id"]

        client.patch(f"/api/v1/bug-fix-tasks/{fix_1_id}", json={"status": "in_progress"})
        client.patch(f"/api/v1/delegations/{delegation_id}", json={"status": "running"})
        client.patch(
            f"/api/v1/delegations/{delegation_id}",
            json={"status": "done", "commit_hash": FIX_1_COMMIT_HASH},
        )
        done_resp = client.patch(
            f"/api/v1/bug-fix-tasks/{fix_1_id}",
            json={"status": "done", "actual_minutes": 8},
        )
        assert done_resp.status_code == 200
        assert done_resp.json()["status"] == "done"

        # --- The orchestrator's pre-PATCH query — "are all fix tasks
        # done?" — must return a non-empty "un-done" result, so the
        # bug PATCH is NOT fired.
        undone = client.get(
            "/api/v1/bug-fix-tasks",
            params={"bug_id": bug_id, "status": "todo"},
        )
        assert undone.status_code == 200
        # FIX-2 is still ``todo``.
        assert undone.json()["total"] == 1
        assert undone.json()["items"][0]["id"] == fix_2_id

        # --- Bug MUST still be ``accepted`` — the §3.17 step 5 gate
        # has not fired. This is the key regression-proof assertion.
        bug_resp = client.get(f"/api/v1/bugs/{bug_id}")
        assert bug_resp.status_code == 200
        assert bug_resp.json()["status"] == "accepted"

        # --- DB state agrees.
        db_session.expire_all()
        persisted_bug = db_session.get(Bug, bug_012.id)
        assert persisted_bug is not None
        assert persisted_bug.status == "accepted"
        # FIX-2 is still ``todo`` at the DB level too.
        persisted_fix_2 = db_session.get(BugFixTask, fix_2.id)
        assert persisted_fix_2 is not None
        assert persisted_fix_2.status == "todo"
        # Only FIX-1 is ``done`` so far.
        persisted_fix_1 = db_session.get(BugFixTask, fix_1.id)
        assert persisted_fix_1 is not None
        assert persisted_fix_1.status == "done"

    def test_cc_failure_keeps_fix_task_undone_and_bug_accepted(
        self,
        client,
        db_session,
        dominik,
        nex_horizont,
        bug_012,
        fix_1,
        fix_2,
    ):
        """§3.17 step 3 gate: a failed CC run does not flip the fix task to ``done``.

        Dominik delegates FIX-1 but the CC subprocess exits
        non-zero (e.g. tests fail, no commit produced). The
        orchestrator PATCHes the delegation to ``status='failed'``
        rather than ``done``; the fix task therefore stays at
        ``in_progress`` (not ``done``); BUG-012 stays at
        ``accepted``. A regression that flipped the fix task
        regardless of CC's exit code — or that advanced the bug
        while a fix task was still un-done — would fail this
        test.
        """
        bug_id = str(bug_012.id)
        fix_1_id = str(fix_1.id)

        # --- Delegate FIX-1: ``pending → running``, then CC fails.
        delegation = client.post(
            "/api/v1/delegations",
            json={
                "bug_fix_task_id": fix_1_id,
                "bug_id": bug_id,
                "cc_agent": "ubuntu_cc",
                "prompt": _fix_prompt(fix_1, bug_012),
                "status": "pending",
            },
        )
        assert delegation.status_code == 201
        delegation_id = delegation.json()["id"]

        # Fix task flipped to ``in_progress`` when delegation started.
        client.patch(f"/api/v1/bug-fix-tasks/{fix_1_id}", json={"status": "in_progress"})
        client.patch(f"/api/v1/delegations/{delegation_id}", json={"status": "running"})

        # --- CC exits non-zero. No commit hash. Delegation PATCHed to
        # ``failed`` — §3.17 step 3 "CC skončí úspešne" gate is red.
        fail_resp = client.patch(
            f"/api/v1/delegations/{delegation_id}",
            json={
                "status": "failed",
                "completed_at": datetime(2026, 4, 16, 9, 30, tzinfo=timezone.utc).isoformat(),
            },
        )
        assert fail_resp.status_code == 200, fail_resp.text
        assert fail_resp.json()["status"] == "failed"
        # No commit hash — CC never produced one.
        assert fail_resp.json()["commit_hash"] is None

        # --- Execution log reflects the failure.
        log_resp = client.post(
            "/api/v1/execution-logs",
            json={
                "delegation_id": delegation_id,
                "status": "failed",
                "duration_seconds": 420,
                "input_tokens": 4_100,
                "output_tokens": 890,
                "total_cost_usd": "0.042150",
                "commit_verified": False,
            },
        )
        assert log_resp.status_code == 201, log_resp.text
        assert log_resp.json()["status"] == "failed"
        assert log_resp.json()["commit_hash"] is None
        assert log_resp.json()["commit_verified"] is False

        # --- Crucially the orchestrator does NOT flip the fix task to
        # ``done``. It stays at ``in_progress`` so Dominik can retry.
        fix_1_resp = client.get(f"/api/v1/bug-fix-tasks/{fix_1_id}")
        assert fix_1_resp.status_code == 200
        assert fix_1_resp.json()["status"] == "in_progress"

        # --- Bug stays ``accepted`` — the "all fix tasks done" signal
        # cannot fire while any task is still ``todo`` / ``in_progress``
        # / ``failed``.
        bug_resp = client.get(f"/api/v1/bugs/{bug_id}")
        assert bug_resp.status_code == 200
        assert bug_resp.json()["status"] == "accepted"

        # --- The failed-delegations inbox surfaces the row — the
        # bugs-page retry UI would drive this exact query.
        failed_inbox = client.get(
            "/api/v1/delegations",
            params={"bug_fix_task_id": fix_1_id, "status": "failed"},
        )
        assert failed_inbox.status_code == 200
        assert failed_inbox.json()["total"] == 1
        assert failed_inbox.json()["items"][0]["id"] == delegation_id

        # --- DB state agrees.
        db_session.expire_all()
        persisted_delegation = db_session.get(Delegation, uuid.UUID(delegation_id))
        assert persisted_delegation is not None
        assert persisted_delegation.status == "failed"
        assert persisted_delegation.commit_hash is None
        # Both pointers still populated — failure does not unlink the
        # delegation from its fix task / bug.
        assert persisted_delegation.bug_fix_task_id == fix_1.id
        assert persisted_delegation.bug_id == bug_012.id

        persisted_fix_1 = db_session.get(BugFixTask, fix_1.id)
        assert persisted_fix_1 is not None
        assert persisted_fix_1.status == "in_progress"

        persisted_fix_2 = db_session.get(BugFixTask, fix_2.id)
        assert persisted_fix_2 is not None
        # FIX-2 was never touched.
        assert persisted_fix_2.status == "todo"

        persisted_bug = db_session.get(Bug, bug_012.id)
        assert persisted_bug is not None
        assert persisted_bug.status == "accepted"

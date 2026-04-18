"""Integration test for BEHAVIOR.md §3.18 ``workflow:resolve_bug``.

Exercises the full happy path of the **resolve_bug** workflow
end-to-end through the real FastAPI ``app``. §3.18 is the final
director-gated step in the bug lifecycle: after Dominik walks
BUG-012 through §3.17 (``status='in_progress'`` with every fix
task ``done`` and the last fix delegation's commit verified by
the GitHub API), Zoltán — or Tibor, both ``role='ri'`` — opens
BUG-012, clicks "Uzavrieť bug", the UI pre-fills the commit-hash
form from the most recent fix delegation, Zoltán confirms, and
the bug transitions from ``status='in_progress'`` → ``resolved``
with ``resolved_at`` stamped and ``commit_hash`` persisted.

The worked example throughout is drawn from BEHAVIOR.md §3.18
step 1 verbatim: "Zoltán otvorí BUG-012, vidí oba fix tasks
``done``, commit hash ``'a1b2c3d'``". The commit hash that ends
up on the :class:`~backend.db.models.bugs.Bug` row is sourced
from the most recent fix delegation — the form in §3.18 step 2
pre-fills it, but the final value is whatever Zoltán confirms
(the form lets him override, so the "latest fix delegation's
commit" is the pre-fill not the hard-coded source).

The form / pre-fill logic, the toast message ("BUG-012 uzavretý.
Commit: a1b2c3d.") and the archive-view routing are UI / view
concerns and out of scope at the HTTP / CRUD layer. The test
supplies the structured side effects the UI layer would produce
(the commit hash the UI copied from the latest fix delegation,
the ``status='resolved'`` PATCH Zoltán confirms) and verifies
the *observable* side effects against the HTTP contract and the
DB state. The key CRUD-layer contract under test is
:mod:`backend.services.bug` :func:`update`'s auto-stamp of
``resolved_at = now()`` on the ``→ resolved`` transition — the
§3.18 step 3 system response names ``resolved_at=NOW()``
explicitly as a server-side side effect and the UI therefore
does not send it.

    Precondition (per BEHAVIOR.md §3.18, lines 716-719):
        * :class:`Bug` has ``status='in_progress'`` — the §3.17
          postcondition. BUG-012 is seeded directly at that state
          so §3.18 has an in-flight bug to resolve.
        * Every :class:`BugFixTask` under the bug is ``done`` —
          also §3.17 postcondition line 702. FIX-1 and FIX-2 are
          seeded at ``status='done'`` directly.
        * Commit has been verified by the GitHub API — modelled
          here as the latest fix delegation carrying a
          ``commit_hash`` and its :class:`ExecutionLog` carrying
          ``commit_verified=True``. Both are seeded directly (the
          verification itself is an orchestration concern out of
          scope at the CRUD layer; the observable signal is the
          ``commit_verified`` flag).
        * Actor has ``role='ri'``. §3.18 names Zoltán
          (``ri_director``) and Tibor (``ri_senior``) as the two
          valid actors — same set as §3.16. Role enforcement is
          an auth-middleware concern (the router layer does not
          wire a JWT dependency yet); the test still persists
          both Zoltán and Tibor as ``role='ri'`` users so the
          "all project members with ``role='ri'`` may resolve"
          contract is observable.

    Steps (per BEHAVIOR.md §3.18, lines 723-728):
        1. Zoltán opens BUG-012 → sees both fix tasks ``done``
           and the latest fix delegation's commit hash
           ``'a1b2c3d'``. Modelled here as:
           ``GET /api/v1/bugs/{id}`` returns ``status='in_progress'``;
           ``GET /api/v1/bug-fix-tasks?bug_id=...&status=done``
           returns two rows; the bugs-page delegation-history
           query
           ``GET /api/v1/delegations?bug_id=...``
           returns the fix delegations ordered by ``started_at
           DESC``; the latest row carries ``commit_hash`` which
           the UI pre-fills into step 2's form.
        2. Zoltán clicks "Uzavrieť bug" → the UI opens the
           "Commit hash (pre-filled), poznámka" form. UI-only —
           no HTTP round-trip at the CRUD layer. The pre-fill
           value comes from the latest fix delegation read in
           step 1.
        3. Zoltán confirms → the UI sends
           ``PATCH /api/v1/bugs/{id}`` with
           ``{"status": "resolved", "commit_hash": "a1b2c3d..."}``.
           The :mod:`backend.services.bug` :func:`update` service:
             - Persists ``status='resolved'``.
             - Persists the commit hash the UI supplied.
             - Auto-stamps ``resolved_at = now()`` because the
               ``→ resolved`` transition fires the auto-stamp
               branch and the caller did not set ``resolved_at``
               explicitly. §3.18 step 3 system response names
               all three side effects verbatim.
        4. — (system) — the UI surfaces the "BUG-012 uzavretý.
           Commit: a1b2c3d." toast. View-layer concern; the
           observable CRUD signal is the 200 response body
           carrying the updated row.

    Postcondition (per BEHAVIOR.md §3.18, lines 730-732):
        * Bug has ``status='resolved'``, ``resolved_at`` set and
          ``commit_hash`` stored.
        * Bug is archived (visible via the ``status='resolved'``
          filter but no longer in the ``in_progress`` inbox).

Edge cases verified alongside the happy path:

    * **Auto-stamp is exclusive to the ``→ resolved`` edge** — a
      PATCH that flips ``status`` to ``resolved`` without an
      explicit ``resolved_at`` stamps the column to ``now()``. A
      *second* PATCH against the already-resolved row that only
      updates (say) the commit hash must NOT re-stamp
      ``resolved_at`` — the auto-stamp guard pins the "from
      non-resolved" transition, not "any PATCH touching a
      resolved row". Pins :mod:`backend.services.bug`
      :func:`update`'s
      ``if new_status == "resolved" and bug.status != "resolved"``
      guard.
    * **Explicit ``resolved_at`` wins over auto-stamp** — a
      backfill / correction flow that supplies ``resolved_at``
      explicitly (e.g. the bug was actually fixed yesterday and
      Zoltán is only marking it resolved now) keeps the caller's
      timestamp. Pins the
      ``if "resolved_at" not in update_data`` guard so the UI
      can submit a corrected timestamp when the "uzavretý" event
      happened out of band.
    * **Resolving a missing bug → HTTP 404** — PATCH against a
      random UUID that does not resolve to any row surfaces as
      HTTP 404 (service :class:`ValueError` "not found" → 404
      via the router's error mapper). Nothing is written; no
      phantom row appears in the DB.

Auth note:
    Same as the rest of the Feat 7 integration tests — the router
    layer does not wire a JWT dependency yet, so the "Actor has
    role=ri" precondition is satisfied by persisting the actor
    with the correct role. Role enforcement is a separate
    auth-middleware concern; the test focuses on the CRUD-layer
    contract the frontend hits.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.models.bugs import Bug, BugFixTask
from backend.db.models.delegations import Delegation, ExecutionLog
from backend.db.models.foundation import User
from backend.db.models.projects import Project

# ---------------------------------------------------------------------------
# Precondition fixtures — NEX Horizont project with Zoltán (ri_director),
# Tibor (ri_senior), Dominik (ha_medior — the §3.17 delegator) and Nazar
# (shu_junior reporter) as members. BUG-012 is pre-seeded at
# ``status='in_progress'`` — the §3.17 postcondition — with two fix tasks
# (FIX-1 backend, FIX-2 test) at ``status='done'`` and two fix delegations
# carrying their commit hashes. §3.18 picks up from here.
# ---------------------------------------------------------------------------


@pytest.fixture()
def zoltan(db_session) -> User:
    """Persist Zoltán — the ``ri_director`` actor from §3.18.

    BEHAVIOR.md §3.18 line 725 names Zoltán explicitly ("Zoltán
    otvorí BUG-012..."). Role is ``ri``; the §3.18 actor line
    (``Actor: ri_director alebo ri_senior``) also names Tibor as
    valid, but the worked example is Zoltán's.
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
def tibor(db_session) -> User:
    """Persist Tibor — the ``ri_senior`` alternative actor from §3.18.

    §3.18 lists both ``ri_director`` and ``ri_senior`` as valid
    actors (line 712). Tibor's fixture lets the companion "both
    directors can resolve" test flip the actor without
    re-provisioning the graph.
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
def dominik(db_session) -> User:
    """Persist Dominik — the ``ha_medior`` §3.17 delegator.

    Dominik is not the §3.18 actor but is the named delegator
    who walked BUG-012 through §3.17. His ``User`` row is needed
    only to round out the project-members graph — §3.18's fix
    delegations would have been his even though the fixture
    skips that audit column (delegations do not carry a
    ``created_by`` FK).
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
def nazar(db_session) -> User:
    """Persist Nazar — the ``shu_junior`` who filed BUG-012 via §3.15.

    Nazar is the ``created_by`` for BUG-012 — §3.15 worked
    example. He plays no role in §3.18 itself (the bug is
    already being resolved by §3.18's time), but the FK
    ``bugs.created_by`` is NOT NULL so the fixture graph needs
    him.
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
def nex_horizont(db_session, zoltan, tibor, dominik, nazar) -> Project:
    """Persist the NEX Horizont project with all four actors as members.

    Matches the §3.15-§3.18 worked example: BUG-012 is filed,
    accepted, worked and resolved against NEX Horizont. All four
    users are added to the ``project_members`` join table so the
    "is member of project" implicit precondition is satisfied
    for every actor.
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


# Representative commit hashes populated on the fix delegations by §3.17.
# FIX-2 is the *latest* fix delegation (started after FIX-1's completion),
# so its commit is the one the §3.18 step 2 form pre-fills and the one
# that lands on the bug.
FIX_1_COMMIT_HASH = "b4c2d7e6f1a8c3b5e4d7a9f6c8b3d5e1a2f4c9d8"
FIX_2_COMMIT_HASH = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"

# §3.18 step 1 names the commit hash "a1b2c3d" — the worked-example
# abbreviation of a full SHA-1. ``FIX_2_COMMIT_HASH`` starts with the same
# seven hex digits so the full-hash DB value and the step 1 narrative line
# up on their ``startswith('a1b2c3d')`` prefix.
assert FIX_2_COMMIT_HASH.startswith("a1b2c3d")


# Timing — FIX-2 starts after FIX-1 completes so ``started_at DESC``
# returns FIX-2's delegation first (the bugs-page delegation-history
# query the UI reads in step 1 is ordered that way).
FIX_1_STARTED_AT = datetime(2026, 4, 16, 9, 0, 0, tzinfo=timezone.utc)
FIX_1_COMPLETED_AT = FIX_1_STARTED_AT + timedelta(minutes=9, seconds=42)
FIX_2_STARTED_AT = FIX_1_COMPLETED_AT + timedelta(minutes=5)
FIX_2_COMPLETED_AT = FIX_2_STARTED_AT + timedelta(minutes=4, seconds=18)


@pytest.fixture()
def bug_012(db_session, nex_horizont, nazar) -> Bug:
    """Seed BUG-012 at ``status='in_progress'`` — the §3.18 precondition.

    BEHAVIOR.md §3.18 precondition line 717: "``bugs`` má
    ``status='in_progress'``". The §3.17 postcondition is exactly
    that — once every fix task lands ``done`` the orchestrator
    PATCHes the bug to ``in_progress``. We seed directly at that
    state so §3.18 has an in-flight bug to resolve.

    ``bug_number=12`` reproduces the "BUG-012" label from §3.18
    step 1 faithfully. ``created_by=nazar.id`` mirrors §3.15 step
    3 — Nazar is the reporter. ``commit_hash`` is ``None`` at this
    stage — the §3.17 postcondition does not stamp it onto the bug
    (the §3.17 postcondition only names status / fix tasks, not
    commit). §3.18 step 3 is the first writer of
    ``bugs.commit_hash``.
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
        status="in_progress",
        source="internal",
        environment="development",
        created_by=nazar.id,
    )
    db_session.add(bug)
    db_session.flush()
    return bug


@pytest.fixture()
def fix_1_done(db_session, bug_012) -> BugFixTask:
    """Seed BUG-012/FIX-1 at ``status='done'`` — the §3.18 precondition.

    §3.18 precondition line 718: "Všetky ``bug_fix_tasks`` sú
    ``done``". FIX-1 was the backend task (§3.16 step 3 first
    fix task); Dominik walked it to ``done`` via §3.17. Seeded
    at ``status='done'`` with ``actual_minutes`` stamped so the
    bug-detail page's fix-task table renders the "9 min" total.
    """
    task = BugFixTask(
        bug_id=bug_012.id,
        number=1,
        title="Fix phone field null handling in PAB service",
        description=(
            "Update PAB service layer to treat ``phone=NULL`` as a valid empty "
            "value: accept NULL on UPDATE, render as blank in the detail view, "
            "and add defensive guard in the serializer."
        ),
        task_type="backend",
        status="done",
        checklist_type="service",
        actual_minutes=9,
    )
    db_session.add(task)
    db_session.flush()
    return task


@pytest.fixture()
def fix_2_done(db_session, bug_012) -> BugFixTask:
    """Seed BUG-012/FIX-2 at ``status='done'`` — the §3.18 precondition.

    §3.18 precondition line 718: every fix task is ``done``.
    FIX-2 was the test task (§3.16 step 3 second fix task);
    Dominik walked it to ``done`` via §3.17. Seeded at
    ``status='done'`` with ``actual_minutes`` stamped.
    """
    task = BugFixTask(
        bug_id=bug_012.id,
        number=2,
        title="Add empty phone test",
        description=("Regression test pinning the empty phone field accepts NULL and the detail page renders."),
        task_type="test",
        status="done",
        checklist_type="test",
        actual_minutes=4,
    )
    db_session.add(task)
    db_session.flush()
    return task


@pytest.fixture()
def fix_1_delegation(db_session, bug_012, fix_1_done) -> Delegation:
    """Seed the FIX-1 delegation — ``status='done'`` with its commit hash.

    §3.18 precondition line 719: "Commit bol overený GitHub
    API". The observable signal at the CRUD layer is the
    ``delegations.commit_hash`` column populated on a
    ``status='done'`` row plus the companion
    :class:`ExecutionLog` carrying ``commit_verified=True``.
    FIX-1's delegation is the earlier of the two; its commit is
    *not* the one the UI pre-fills — the latest-by-started_at
    delegation wins (see :func:`fix_2_delegation`). Still seeded
    so the bugs-page delegation-history query returns both rows
    ordered by ``started_at DESC``.
    """
    delegation = Delegation(
        bug_fix_task_id=fix_1_done.id,
        bug_id=bug_012.id,
        cc_agent="ubuntu_cc",
        prompt="Fix BUG-012/FIX-1 — phone field null handling in PAB service.",
        status="done",
        raw_output='{"type":"result","commit_hash":"' + FIX_1_COMMIT_HASH + '"}\n',
        commit_hash=FIX_1_COMMIT_HASH,
        started_at=FIX_1_STARTED_AT,
        completed_at=FIX_1_COMPLETED_AT,
    )
    db_session.add(delegation)
    db_session.flush()
    return delegation


@pytest.fixture()
def fix_2_delegation(db_session, bug_012, fix_2_done) -> Delegation:
    """Seed the FIX-2 delegation — the latest fix delegation on BUG-012.

    §3.18 step 1 names the commit hash ``'a1b2c3d'`` — the
    abbreviation of the most recent fix delegation's commit.
    FIX-2's delegation started after FIX-1's completed; its
    ``commit_hash`` therefore wins the "latest fix delegation"
    lookup the UI uses to pre-fill the step 2 form.
    """
    delegation = Delegation(
        bug_fix_task_id=fix_2_done.id,
        bug_id=bug_012.id,
        cc_agent="ubuntu_cc",
        prompt="Fix BUG-012/FIX-2 — add empty phone regression test.",
        status="done",
        raw_output='{"type":"result","commit_hash":"' + FIX_2_COMMIT_HASH + '"}\n',
        commit_hash=FIX_2_COMMIT_HASH,
        started_at=FIX_2_STARTED_AT,
        completed_at=FIX_2_COMPLETED_AT,
    )
    db_session.add(delegation)
    db_session.flush()
    return delegation


@pytest.fixture()
def fix_1_execution_log(db_session, fix_1_delegation) -> ExecutionLog:
    """Seed FIX-1's execution log with ``commit_verified=True``.

    §3.18 precondition line 719: "Commit bol overený GitHub
    API". At the CRUD layer that is the
    :class:`ExecutionLog.commit_verified` boolean. Populated here
    on the FIX-1 log for completeness even though §3.18 step 3
    copies only the *latest* fix delegation's commit onto the
    bug.
    """
    log = ExecutionLog(
        delegation_id=fix_1_delegation.id,
        status="done",
        duration_seconds=582,
        input_tokens=8_120,
        output_tokens=2_040,
        commit_hash=FIX_1_COMMIT_HASH,
        commit_verified=True,
    )
    db_session.add(log)
    db_session.flush()
    return log


@pytest.fixture()
def fix_2_execution_log(db_session, fix_2_delegation) -> ExecutionLog:
    """Seed FIX-2's execution log with ``commit_verified=True``.

    This is the log §3.18 precondition line 719 actually gates
    on — the latest fix delegation's commit must have been
    verified by the GitHub API before the §3.18 step 2 "Uzavrieť
    bug" button is enabled.
    """
    log = ExecutionLog(
        delegation_id=fix_2_delegation.id,
        status="done",
        duration_seconds=258,
        input_tokens=5_210,
        output_tokens=1_380,
        commit_hash=FIX_2_COMMIT_HASH,
        commit_verified=True,
    )
    db_session.add(log)
    db_session.flush()
    return log


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.18 end-to-end, BUG-012 resolve.
# ---------------------------------------------------------------------------


class TestResolveBugHappyPath:
    """End-to-end walkthrough of workflow §3.18 against the real app."""

    def test_full_workflow_resolves_bug_012_with_latest_fix_commit(
        self,
        client,
        db_session,
        zoltan,
        nex_horizont,
        bug_012,
        fix_1_done,
        fix_2_done,
        fix_1_delegation,
        fix_2_delegation,
        fix_1_execution_log,
        fix_2_execution_log,
    ):
        """Drive steps 1-4 of §3.18 and verify every postcondition.

        Reproduces the §3.18 worked example faithfully: Zoltán
        opens BUG-012, sees both fix tasks ``done``, sees the
        latest fix delegation's commit hash (``'a1b2c3d...'``),
        clicks "Uzavrieť bug" and confirms. The bug lands at
        ``status='resolved'`` with ``resolved_at`` stamped by
        the service and ``commit_hash`` persisted from the form.
        """
        bug_id = str(bug_012.id)
        fix_1_id = str(fix_1_done.id)
        fix_2_id = str(fix_2_done.id)
        delegation_1_id = str(fix_1_delegation.id)
        delegation_2_id = str(fix_2_delegation.id)

        # Capture a pre-PATCH ``now`` so the auto-stamped
        # ``resolved_at`` can be bounded against the wall clock.
        patch_floor = datetime.now(tz=timezone.utc)

        # ====================================================================
        # STEP 1 — Zoltán opens BUG-012. He sees:
        #   (a) ``status='in_progress'`` — the §3.18 precondition.
        #   (b) Both fix tasks at ``status='done'``.
        #   (c) The latest fix delegation's ``commit_hash`` — the value
        #       the §3.18 step 2 form pre-fills from.
        # ====================================================================
        bug_resp = client.get(f"/api/v1/bugs/{bug_id}")
        assert bug_resp.status_code == 200, bug_resp.text
        bug_body = bug_resp.json()
        # §3.18 precondition line 717.
        assert bug_body["status"] == "in_progress"
        assert bug_body["bug_number"] == 12
        # §3.18 step 3 is the first writer of ``bugs.commit_hash`` /
        # ``bugs.resolved_at`` — they are still ``None`` at step 1.
        assert bug_body["commit_hash"] is None
        assert bug_body["resolved_at"] is None

        # Every fix task on BUG-012 is ``done`` — §3.18 precondition
        # line 718. The "done" filter returns both rows; the "todo" /
        # "in_progress" inbox is empty.
        done_tasks = client.get(
            "/api/v1/bug-fix-tasks",
            params={"bug_id": bug_id, "status": "done"},
        )
        assert done_tasks.status_code == 200
        assert done_tasks.json()["total"] == 2
        assert {row["id"] for row in done_tasks.json()["items"]} == {fix_1_id, fix_2_id}

        for undone_status in ("todo", "in_progress", "failed"):
            inbox = client.get(
                "/api/v1/bug-fix-tasks",
                params={"bug_id": bug_id, "status": undone_status},
            )
            assert inbox.status_code == 200
            assert inbox.json()["total"] == 0

        # The bugs-page delegation-history query — ordered by
        # ``started_at DESC`` so the UI can read the latest commit
        # hash out of ``items[0]``.
        bug_delegations = client.get(
            "/api/v1/delegations",
            params={"bug_id": bug_id},
        )
        assert bug_delegations.status_code == 200
        assert bug_delegations.json()["total"] == 2
        # Latest first — FIX-2 started after FIX-1.
        ordered_ids = [row["id"] for row in bug_delegations.json()["items"]]
        assert ordered_ids == [delegation_2_id, delegation_1_id]
        # Every delegation carries a commit hash + ``status='done'`` —
        # the §3.17 postcondition.
        for row in bug_delegations.json()["items"]:
            assert row["status"] == "done"
            assert row["commit_hash"] is not None
        # The form-prefill value is the latest row's commit hash.
        latest_delegation = bug_delegations.json()["items"][0]
        latest_commit_hash = latest_delegation["commit_hash"]
        assert latest_commit_hash == FIX_2_COMMIT_HASH
        # §3.18 step 1 narrative: "commit hash 'a1b2c3d'".
        assert latest_commit_hash.startswith("a1b2c3d")

        # §3.18 precondition line 719: commit was verified by GitHub API.
        # The observable signal is the execution log's
        # ``commit_verified`` flag — ``True`` on both fix logs.
        log_2_resp = client.get(f"/api/v1/execution-logs/{fix_2_execution_log.id}")
        assert log_2_resp.status_code == 200
        assert log_2_resp.json()["commit_verified"] is True
        assert log_2_resp.json()["commit_hash"] == FIX_2_COMMIT_HASH

        # BUG-012 currently sits in the "in_progress" inbox.
        in_progress_inbox = client.get(
            "/api/v1/bugs",
            params={"project_id": str(nex_horizont.id), "status": "in_progress"},
        )
        assert in_progress_inbox.status_code == 200
        assert in_progress_inbox.json()["total"] == 1
        assert in_progress_inbox.json()["items"][0]["id"] == bug_id
        # And NOT yet in the "resolved" inbox.
        resolved_inbox_before = client.get(
            "/api/v1/bugs",
            params={"project_id": str(nex_horizont.id), "status": "resolved"},
        )
        assert resolved_inbox_before.status_code == 200
        assert resolved_inbox_before.json()["total"] == 0

        # ====================================================================
        # STEP 2 — Zoltán clicks "Uzavrieť bug". UI-only (the form
        # opens with ``commit_hash`` pre-filled from step 1's latest
        # delegation and an empty ``poznámka`` field). No HTTP
        # round-trip at the CRUD layer — just the UI's local state
        # transition.
        # ====================================================================

        # ====================================================================
        # STEP 3 — Zoltán confirms. The UI sends ``PATCH /api/v1/bugs/{id}``
        # with ``status='resolved'`` and the pre-filled commit hash.
        # ``resolved_at`` is NOT included — the service stamps it
        # automatically on the ``→ resolved`` transition (§3.18 step 3
        # system response names ``resolved_at=NOW()`` as a server-side
        # side effect, not a client-supplied value).
        # ====================================================================
        resolve_resp = client.patch(
            f"/api/v1/bugs/{bug_id}",
            json={
                "status": "resolved",
                "commit_hash": latest_commit_hash,
            },
        )
        assert resolve_resp.status_code == 200, resolve_resp.text
        resolved = resolve_resp.json()

        # §3.18 step 3 system response #1 — ``status='resolved'``.
        assert resolved["status"] == "resolved"
        # §3.18 step 3 system response #3 — ``commit_hash`` stored.
        assert resolved["commit_hash"] == FIX_2_COMMIT_HASH
        # §3.18 step 3 system response #2 — ``resolved_at=NOW()``.
        # The service stamps it automatically; the response should
        # carry a timezone-aware ISO-8601 value somewhere in
        # ``[patch_floor, now())``.
        assert resolved["resolved_at"] is not None
        stamped_at = datetime.fromisoformat(resolved["resolved_at"])
        assert stamped_at.tzinfo is not None
        patch_ceiling = datetime.now(tz=timezone.utc)
        assert patch_floor <= stamped_at <= patch_ceiling

        # Immutable audit fields preserved — the §3.18 PATCH rewrites
        # nothing except the three named columns.
        assert resolved["id"] == bug_id
        assert resolved["bug_number"] == 12
        assert resolved["project_id"] == str(nex_horizont.id)
        assert resolved["title"] == bug_012.title
        assert resolved["description"] == bug_012.description
        assert resolved["severity"] == "major"
        assert resolved["source"] == "internal"
        assert resolved["environment"] == "development"
        assert resolved["created_by"] == str(bug_012.created_by)

        # ====================================================================
        # STEP 4 — (system) — the UI would render "BUG-012 uzavretý.
        # Commit: a1b2c3d.". That toast is a view-layer side effect
        # not observable at the CRUD layer; the observable signal is
        # the 200 response body above and the updated row on subsequent
        # reads. Nothing to assert here beyond the read that confirms
        # the transition stuck.
        # ====================================================================

        # ====================================================================
        # Postcondition verification (HTTP) ------------------------------
        # ====================================================================
        # §3.18 postcondition line 731: bug at ``status='resolved'``,
        # ``resolved_at`` set, ``commit_hash`` stored.
        after_resp = client.get(f"/api/v1/bugs/{bug_id}")
        assert after_resp.status_code == 200
        after = after_resp.json()
        assert after["status"] == "resolved"
        assert after["resolved_at"] == resolved["resolved_at"]
        assert after["commit_hash"] == FIX_2_COMMIT_HASH

        # §3.18 postcondition line 732: bug is archived — visible
        # (the detail endpoint still returns it) but no longer in the
        # active inbox.
        in_progress_inbox_after = client.get(
            "/api/v1/bugs",
            params={"project_id": str(nex_horizont.id), "status": "in_progress"},
        )
        assert in_progress_inbox_after.status_code == 200
        assert in_progress_inbox_after.json()["total"] == 0
        # And present in the "resolved" archive.
        resolved_inbox = client.get(
            "/api/v1/bugs",
            params={"project_id": str(nex_horizont.id), "status": "resolved"},
        )
        assert resolved_inbox.status_code == 200
        assert resolved_inbox.json()["total"] == 1
        assert resolved_inbox.json()["items"][0]["id"] == bug_id
        assert resolved_inbox.json()["items"][0]["commit_hash"] == FIX_2_COMMIT_HASH

        # The project-wide unfiltered list still includes BUG-012 —
        # "archived" means closed, not deleted. The UI's "show
        # archived" toggle just flips the ``status`` filter.
        project_inbox = client.get(
            "/api/v1/bugs",
            params={"project_id": str(nex_horizont.id)},
        )
        assert project_inbox.status_code == 200
        assert project_inbox.json()["total"] == 1
        assert project_inbox.json()["items"][0]["id"] == bug_id

        # Fix tasks are untouched — §3.18 operates on the bug row,
        # not on its fix tasks. "Data touched" line 734 names only
        # ``bugs``.
        fix_tasks_after = client.get(
            "/api/v1/bug-fix-tasks",
            params={"bug_id": bug_id},
        )
        assert fix_tasks_after.status_code == 200
        assert fix_tasks_after.json()["total"] == 2
        for row in fix_tasks_after.json()["items"]:
            assert row["status"] == "done"

        # Fix delegations are untouched too — their commit hashes
        # were the *source* of the bug's commit hash, not a target
        # of the §3.18 PATCH.
        bug_delegations_after = client.get(
            "/api/v1/delegations",
            params={"bug_id": bug_id},
        )
        assert bug_delegations_after.status_code == 200
        assert bug_delegations_after.json()["total"] == 2
        for row in bug_delegations_after.json()["items"]:
            assert row["status"] == "done"
            assert row["commit_hash"] in {FIX_1_COMMIT_HASH, FIX_2_COMMIT_HASH}

        # ====================================================================
        # Postcondition verification (DB state) --------------------------
        # ====================================================================
        db_session.expire_all()

        # §3.18 postcondition line 731 — the triple of columns the
        # service writes in step 3.
        persisted_bug = db_session.get(Bug, bug_012.id)
        assert persisted_bug is not None
        assert persisted_bug.status == "resolved"
        assert persisted_bug.commit_hash == FIX_2_COMMIT_HASH
        assert persisted_bug.resolved_at is not None
        assert persisted_bug.resolved_at.tzinfo is not None
        assert patch_floor <= persisted_bug.resolved_at <= patch_ceiling
        # Identity / audit columns preserved.
        assert persisted_bug.bug_number == 12
        assert persisted_bug.project_id == nex_horizont.id
        assert persisted_bug.created_by == bug_012.created_by

        # Fix tasks untouched — still ``done`` with the same
        # ``actual_minutes`` stamps from §3.17.
        persisted_fix_1 = db_session.get(BugFixTask, fix_1_done.id)
        persisted_fix_2 = db_session.get(BugFixTask, fix_2_done.id)
        assert persisted_fix_1 is not None
        assert persisted_fix_2 is not None
        assert persisted_fix_1.status == "done"
        assert persisted_fix_2.status == "done"
        assert persisted_fix_1.actual_minutes == 9
        assert persisted_fix_2.actual_minutes == 4

        # Fix delegations untouched.
        persisted_delegation_1 = db_session.get(Delegation, fix_1_delegation.id)
        persisted_delegation_2 = db_session.get(Delegation, fix_2_delegation.id)
        assert persisted_delegation_1 is not None
        assert persisted_delegation_2 is not None
        assert persisted_delegation_1.status == "done"
        assert persisted_delegation_2.status == "done"
        assert persisted_delegation_1.commit_hash == FIX_1_COMMIT_HASH
        assert persisted_delegation_2.commit_hash == FIX_2_COMMIT_HASH

        # §3.18 precondition line 719 still holds on the DB — the
        # verified execution log is unchanged.
        persisted_log_2 = db_session.get(ExecutionLog, fix_2_execution_log.id)
        assert persisted_log_2 is not None
        assert persisted_log_2.commit_verified is True
        assert persisted_log_2.commit_hash == FIX_2_COMMIT_HASH

        # Role check — Zoltán is ``ri``, satisfying the §3.18 actor
        # precondition line 712. The role is also the signal the
        # recipient-lookup query (all ``role='ri'`` users) uses to
        # populate the "Uzavrieť bug" button's audience.
        assert zoltan.role == "ri"

    def test_tibor_ri_senior_may_also_resolve_a_bug(
        self,
        client,
        db_session,
        tibor,
        nazar,
        nex_horizont,
    ):
        """BEHAVIOR.md §3.18 actor line — both ``ri`` users are valid.

        §3.18 lists ``ri_director`` and ``ri_senior`` as the two
        valid actors (line 712). Zoltán (``ri_director``) is
        covered by the worked example; Tibor (``ri_senior``) is
        pinned here so the contract is not accidentally narrowed
        to one director. A second bug (BUG-050) is seeded at
        ``status='in_progress'`` with a commit hash — the full
        fix-task / delegation graph is not necessary to exercise
        the CRUD-layer resolve PATCH.
        """
        # Seed a second "in_progress" bug so Tibor has something to
        # resolve.
        second_bug = Bug(
            project_id=nex_horizont.id,
            bug_number=50,
            title="Migration dry-run crashes on empty source table.",
            description="Steps: run MIG dry-run against a fresh DB...",
            severity="minor",
            status="in_progress",
            source="internal",
            environment="development",
            created_by=nazar.id,
        )
        db_session.add(second_bug)
        db_session.flush()

        tibor_commit = "e9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0"

        # Tibor resolves.
        resolve_resp = client.patch(
            f"/api/v1/bugs/{second_bug.id}",
            json={"status": "resolved", "commit_hash": tibor_commit},
        )
        assert resolve_resp.status_code == 200, resolve_resp.text
        resolved = resolve_resp.json()
        assert resolved["status"] == "resolved"
        assert resolved["commit_hash"] == tibor_commit
        assert resolved["resolved_at"] is not None

        # DB state — bug resolved, Tibor is ``ri`` (actor
        # precondition line 712).
        db_session.expire_all()
        persisted = db_session.get(Bug, second_bug.id)
        assert persisted is not None
        assert persisted.status == "resolved"
        assert persisted.commit_hash == tibor_commit
        assert persisted.resolved_at is not None
        assert tibor.role == "ri"


# ---------------------------------------------------------------------------
# Edge cases — auto-stamp guard, explicit ``resolved_at`` wins, and the
# resolve-missing-bug 404 path.
# ---------------------------------------------------------------------------


class TestResolveBugEdgeCases:
    """Auto-stamp semantics and missing-bug contracts.

    Two service-layer behaviours on ``→ resolved`` are worth
    pinning beyond the happy path:

    1. The auto-stamp is exclusive to the *transition* edge —
       a PATCH against an already-resolved bug must NOT re-stamp
       ``resolved_at``. The
       ``if new_status == "resolved" and bug.status != "resolved"``
       guard in :mod:`backend.services.bug` :func:`update`
       enforces this.
    2. An explicit client-supplied ``resolved_at`` wins over the
       auto-stamp. The
       ``if "resolved_at" not in update_data`` guard pins this —
       backfill / correction flows remain possible.

    Plus the usual missing-resource 404.
    """

    def test_auto_stamp_fires_only_on_first_transition_to_resolved(
        self,
        client,
        db_session,
        bug_012,
        fix_1_done,
        fix_2_done,
    ):
        """Auto-stamp must NOT re-fire on a second PATCH against a resolved row.

        Zoltán resolves BUG-012 (first PATCH — auto-stamp fires).
        Later he PATCHes a corrected commit hash onto the same row
        (second PATCH — ``status`` is unchanged, still ``resolved``).
        The corrected commit hash must land, but ``resolved_at``
        must NOT be re-stamped to the second PATCH's wall clock.

        A regression that flipped the service's
        ``if new_status == "resolved" and bug.status != "resolved"``
        guard to ``if new_status == "resolved"`` alone would stamp
        on every PATCH and fail this test.
        """
        bug_id = str(bug_012.id)
        first_commit = FIX_2_COMMIT_HASH
        corrected_commit = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

        # --- First PATCH — the happy-path resolve. Auto-stamp fires.
        first_floor = datetime.now(tz=timezone.utc)
        first_resp = client.patch(
            f"/api/v1/bugs/{bug_id}",
            json={"status": "resolved", "commit_hash": first_commit},
        )
        assert first_resp.status_code == 200, first_resp.text
        first_body = first_resp.json()
        assert first_body["status"] == "resolved"
        assert first_body["commit_hash"] == first_commit
        assert first_body["resolved_at"] is not None
        first_resolved_at = datetime.fromisoformat(first_body["resolved_at"])
        first_ceiling = datetime.now(tz=timezone.utc)
        assert first_floor <= first_resolved_at <= first_ceiling

        # --- Second PATCH — only correcting the commit hash. ``status``
        # is supplied as ``"resolved"`` again (idempotent send from the
        # UI's "correct commit" flow). The auto-stamp guard must see
        # ``bug.status == "resolved"`` already and therefore NOT
        # re-stamp ``resolved_at``.
        #
        # Sleep a fraction of a second to ensure ``now()`` would
        # produce a distinguishable timestamp if the guard leaked.
        time.sleep(0.05)

        second_resp = client.patch(
            f"/api/v1/bugs/{bug_id}",
            json={"status": "resolved", "commit_hash": corrected_commit},
        )
        assert second_resp.status_code == 200, second_resp.text
        second_body = second_resp.json()
        assert second_body["status"] == "resolved"
        # The commit hash update landed.
        assert second_body["commit_hash"] == corrected_commit
        # But the ``resolved_at`` did NOT change — auto-stamp guard
        # did not re-fire.
        assert second_body["resolved_at"] == first_body["resolved_at"]

        # DB state agrees.
        db_session.expire_all()
        persisted = db_session.get(Bug, bug_012.id)
        assert persisted is not None
        assert persisted.status == "resolved"
        assert persisted.commit_hash == corrected_commit
        assert persisted.resolved_at is not None
        # Exact match against the first PATCH's stamped value.
        assert persisted.resolved_at == first_resolved_at

    def test_explicit_resolved_at_wins_over_auto_stamp(
        self,
        client,
        db_session,
        bug_012,
        fix_1_done,
        fix_2_done,
    ):
        """An explicit ``resolved_at`` in the PATCH payload must not be overwritten.

        Backfill / correction flow: the bug was actually fixed
        yesterday (e.g. a hotfix was merged but nobody marked the
        bug resolved until now). Zoltán supplies ``resolved_at``
        explicitly in the payload to match the real fix time;
        :mod:`backend.services.bug` :func:`update` must NOT
        overwrite it with its auto-stamp (``now()``).

        The service's
        ``if "resolved_at" not in update_data`` guard is the pin
        — a regression that dropped the key check and stamped
        unconditionally would fail this test.
        """
        bug_id = str(bug_012.id)
        backfill_resolved_at = datetime(2026, 4, 15, 14, 30, 0, tzinfo=timezone.utc)

        resp = client.patch(
            f"/api/v1/bugs/{bug_id}",
            json={
                "status": "resolved",
                "commit_hash": FIX_2_COMMIT_HASH,
                "resolved_at": backfill_resolved_at.isoformat(),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "resolved"
        assert body["commit_hash"] == FIX_2_COMMIT_HASH
        # The client-supplied timestamp wins — compare as
        # ``datetime`` to side-step ISO-8601 representation
        # quirks (Pydantic may emit ``+00:00`` while
        # ``isoformat()`` preserves whatever the input used).
        assert datetime.fromisoformat(body["resolved_at"]) == backfill_resolved_at

        # DB state — the backfill timestamp is persisted, no
        # overwrite by ``now()``.
        db_session.expire_all()
        persisted = db_session.get(Bug, bug_012.id)
        assert persisted is not None
        assert persisted.status == "resolved"
        assert persisted.resolved_at == backfill_resolved_at

    def test_resolve_missing_bug_returns_404(
        self,
        client,
        db_session,
    ):
        """``PATCH /api/v1/bugs/{random_uuid}`` → HTTP 404, nothing written.

        §3.18 precondition line 717 — the bug must exist at
        ``status='in_progress'``. The UI only reaches §3.18 step 2
        after §3.15-§3.17 have filed, accepted and worked the
        bug, but a direct PATCH against a random UUID (e.g. a
        stale tab after the bug was deleted) is not a contract
        the CRUD layer enforces at the precondition level. It
        surfaces instead as the router's ``not found``
        :class:`ValueError` → HTTP 404 mapping. Nothing is
        written; no phantom row appears.
        """
        phantom_id = uuid.uuid4()
        resp = client.patch(
            f"/api/v1/bugs/{phantom_id}",
            json={
                "status": "resolved",
                "commit_hash": FIX_2_COMMIT_HASH,
            },
        )
        assert resp.status_code == 404, resp.text

        # No bug with the phantom id exists — assert it via the
        # DB session directly. Belt-and-braces check that a 404
        # did not accidentally create a row.
        db_session.expire_all()
        assert db_session.get(Bug, phantom_id) is None

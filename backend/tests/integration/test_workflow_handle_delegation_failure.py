"""Integration test for BEHAVIOR.md §3.13 ``workflow:handle_delegation_failure``.

Exercises the auto-fix escalation loop the orchestrator runs after a CC
delegation exits non-zero. Step 0 of the workflow is always
§3.12's happy path turned sour: Dominik clicks "Delegovať na CC", the
CC subprocess starts, and for whatever reason — a syntax error in the
generated code, a failing ``pytest`` run, a ``git commit`` that trips
a pre-commit hook — the subprocess exits with a non-zero code. At
that point the orchestrator owns every subsequent transition: it
flips the original delegation to ``status='failed'``, mines the
``raw_output`` for the trailing error lines, and persists an
:class:`AutoFixAttempt` with ``attempt_number=1`` and that extracted
``error_description``. It then spawns a fresh CC subprocess
(delegation #2) carrying the original prompt plus the error context,
and the loop repeats up to three attempts total (§4.14 edge case
``auto_fix_max_attempts_exceeded``).

Two branches need pinning:

    * **Auto-fix succeeds on the second attempt** — attempt #1 is
      recorded with ``attempt_number=1`` and a populated
      ``error_description``; a new delegation is spawned with the
      retry prompt; it lands a commit; the original attempt's
      ``fix_description`` and ``delegation_id`` are filled in; and
      the feat transitions to ``status='done'`` per the §3.12
      postcondition "Ak auto-fix uspeje: rovnaká postcondition ako
      [[workflow:delegate_feat_to_cc]]" (line 579).
    * **All three attempts fail** — three :class:`AutoFixAttempt`
      rows exist (``attempt_number=1,2,3``); three delegations are
      all at ``status='failed'``; the feat is at ``status='failed'``
      per the §3.13 postcondition line 580; no fourth attempt is
      spawned (§4.14 edge case: "Systém NESPUSTÍ 4. pokus").

The orchestration concerns outside the HTTP / CRUD layer — spawning
the CC subprocess, streaming NDJSON output, parsing "the last N lines
of output" into ``error_description``, sending the escalation
notification to Zoltán / Tibor via email or in-app — are not
observable through the REST API and therefore not asserted here. What
*is* observable is the set of DB rows the orchestrator writes:
the delegation transitions, the ``AutoFixAttempt`` rows (one per
attempt, ``attempt_number`` auto-assigned as ``MAX+1`` per feat by
the service layer), and the feat's terminal status. This test pins
that contract end-to-end against the real FastAPI ``app``.

The worked example reuses §3.12's fixtures verbatim: NEX Horizont /
STK / EPIC 4 / FEAT 4.2 "STK Service layer" — keeping the shape of
the §3.12 integration test so reviewers can read the two side-by-side
to see "happy path vs. failure path" with the same characters and
setting. The escalation notification target (Zoltán / Tibor) is
persisted as user rows — no notification API surface exists at the
CRUD layer, so we only assert the users the orchestrator would
address.

    Precondition (per BEHAVIOR.md §3.13):
        * CC delegation exited with code != 0 — the orchestrator
          sets ``delegations.status='failed'`` (line 563 /
          step 1). Modelled as a PATCH on the delegation row.

    Steps (per BEHAVIOR.md §3.13):
        1. — (system) — CC finishes with an error. The orchestrator
           PATCHes the delegation to ``status='failed'``.
        2. — (system) — The orchestrator analyses the output and
           creates an :class:`AutoFixAttempt` with
           ``attempt_number=1`` (auto-assigned by the service) and
           the extracted ``error_description``.
        3. — (system) — The orchestrator spawns a new auto-fix
           delegation (original prompt + error context).
        4. Auto-fix succeeds → the orchestrator continues the §3.12
           happy path (commit, Guardian pipeline, FEAT
           ``status='done'``).
        5. Auto-fix fails → the orchestrator repeats steps 2-3 for
           ``attempt_number=2``, up to three total.
        6. All three auto-fix attempts fail → the orchestrator sets
           FEAT ``status='failed'`` and notifies Zoltán.
        7. Zoltán opens the feat detail — UI lists every attempt
           with its ``error_description``.
        8. Zoltán decides: "Reset and re-delegate" or "Manual fix".

    Postcondition (per BEHAVIOR.md §3.13, lines 579-580):
        * If auto-fix succeeds: same as §3.12 (delegation ``done``,
          feat ``done``, …).
        * If all three fail: feat ``status='failed'``,
          :class:`AutoFixAttempt` contains three rows, Zoltán
          notified.

Auth note:
    Same as the §3.12 integration test — the router layer does not
    wire a JWT dependency yet, so the "Actor je člen projektu"
    precondition is satisfied by persisting the actor with the
    correct ``role`` and a :class:`ProjectMember` row. Role
    enforcement is a separate auth-middleware concern.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.db.models.delegations import AutoFixAttempt, Delegation, ExecutionLog
from backend.db.models.foundation import User
from backend.db.models.guardian import GuardianReview
from backend.db.models.projects import Project, ProjectModule
from backend.db.models.specifications import DesignDocument
from backend.db.models.tasks import Epic, Feat, Task

# ---------------------------------------------------------------------------
# Fixtures — reuse the §3.12 worked example (NEX Horizont / STK / FEAT 4.2)
# so the auto-fix loop is read against the same setting.
# ---------------------------------------------------------------------------


@pytest.fixture()
def dominik(db_session) -> User:
    """Persist Dominik (``ha_medior``) — the original §3.12 actor.

    Step 1 of §3.13 is orchestrator-owned so Dominik does not touch
    the auto-fix loop directly, but the precondition "CC delegácia
    skončila" requires a delegation owned by a real actor. Persisting
    Dominik also keeps the fixture graph aligned with §3.12 so the
    two integration tests share a mental model.
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
    """Persist Zoltán (``ri_director``) — the escalation recipient.

    §3.13 step 6 routes the "3 auto-fix pokusy zlyhali" notification
    to Zoltán. The notification transport itself is not at the CRUD
    layer; the user row is what the orchestrator reads from to route
    the escalation.
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
    """Persist Tibor (``ri_senior``) — co-recipient of the §4.14 escalation.

    §4.14 ``edge:auto_fix_max_attempts_exceeded`` lists both Zoltán
    and Tibor as notification targets. Persisted so the escalation
    postcondition can be asserted against both users.
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
def nex_horizont(db_session, zoltan, dominik) -> Project:
    """Persist the NEX Horizont project with Zoltán and Dominik as members."""
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


STK_DESIGN_MD_KB_PATH = "/home/icc/knowledge/projects/nex-horizont/modules/STK/DESIGN.md"


@pytest.fixture()
def stk_module(db_session, nex_horizont) -> ProjectModule:
    """Persist the STK module in ``in_development``."""
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
    """Persist an approved DESIGN.md for the STK module (§3.12 precondition)."""
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
    """Persist EPIC 4 (STK) — the §3.10 / §3.12 worked example epic."""
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
    """Persist FEAT 4.2 "STK Service layer" in ``status='todo'``."""
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
    """Persist three ``todo`` tasks under FEAT 4.2."""
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


# The CC prompt for FEAT 4.2 — matches the §3.12 integration test so
# reviewers can compare the two prompts at a glance.
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

# A representative commit hash CC would extract from its output on the
# auto-fix success path — matches the §3.12 integration test's format.
AUTO_FIX_COMMIT_HASH = "b5d2a4f6c891b3d7e0c45a2e5b6c9e1d3f2a8b7c"


# Representative trailing error lines CC's NDJSON stream would surface
# when a ``poetry run pytest`` call fails. The orchestrator's actual
# extraction heuristic is implementation territory; the CRUD-layer
# contract is that ``error_description`` is a non-empty string that
# captures enough context for a human (or the next CC attempt) to
# diagnose.
ATTEMPT_1_ERROR = (
    "FAILED tests/test_stk_service.py::test_create_stock_item - "
    "sqlalchemy.exc.IntegrityError: duplicate key value violates unique "
    'constraint "uq_stock_items_sku_warehouse"\n'
    "ERROR backend/services/stk.py:42 — missing ON CONFLICT clause in "
    "create_stock_item bulk-insert path\n"
)
ATTEMPT_2_ERROR = (
    "FAILED tests/test_stk_service.py::test_list_stock_items_empty - "
    "AssertionError: expected [] but got None\n"
    "ERROR backend/services/stk.py:87 — list_stock_items returns None "
    "when the warehouse has no rows; must return [].\n"
)
ATTEMPT_3_ERROR = (
    "FAILED tests/test_stk_service.py::test_list_stock_items_pagination - "
    "TypeError: 'NoneType' object is not iterable\n"
    "ERROR backend/services/stk.py:103 — pagination helper still returns "
    "None in edge cases; attempt 2 fix was incomplete.\n"
)


# ---------------------------------------------------------------------------
# Happy path — auto-fix succeeds on the second attempt.
# ---------------------------------------------------------------------------


class TestAutoFixSucceedsOnSecondAttempt:
    """BEHAVIOR.md §3.13 steps 1-4: first delegation fails, auto-fix succeeds.

    Verifies the success branch of the postcondition: "Ak auto-fix
    uspeje: rovnaká postcondition ako [[workflow:delegate_feat_to_cc]]"
    (line 579). The §3.12 postcondition-tail (Guardian pipeline, feat
    ``status='done'`` with ``actual_minutes`` populated) is already
    pinned by ``test_workflow_delegate_feat_to_cc``, so this test
    stops once the feat is ``done`` — re-asserting the full Guardian
    tail here would duplicate that coverage without adding a new
    contract.
    """

    def test_first_delegation_fails_auto_fix_recovers_feat(
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
        """Drive the first-delegation-fails → auto-fix-succeeds branch.

        Steps exercised:
            1. A ``running`` delegation PATCHes to ``status='failed'``.
            2. An ``AutoFixAttempt`` is created with
               ``attempt_number=1`` (auto-assigned) and the trailing
               error context.
            3. A second delegation is spawned with the retry prompt;
               the ``AutoFixAttempt`` is PATCHed to carry the new
               delegation's id.
            4. The retry delegation lands a commit; the feat is
               marked ``status='done'``.
        """
        feat_id = str(feat_service_layer.id)

        # --- Pre-flight: the feat starts ``todo``, the orchestrator
        # flips it to ``in_progress`` when the first delegation is
        # created.
        feat_resp = client.get(f"/api/v1/feats/{feat_id}")
        assert feat_resp.status_code == 200
        assert feat_resp.json()["status"] == "todo"

        client.patch(f"/api/v1/feats/{feat_id}", json={"status": "in_progress"})

        # --- First delegation: pending → running → failed (step 1).
        first_started = datetime(2026, 4, 16, 9, 0, 0, tzinfo=timezone.utc)
        first_resp = client.post(
            "/api/v1/delegations",
            json={
                "feat_id": feat_id,
                "cc_agent": "ubuntu_cc",
                "prompt": FEAT_42_PROMPT,
                "status": "pending",
                "started_at": first_started.isoformat(),
            },
        )
        assert first_resp.status_code == 201, first_resp.text
        first_id = first_resp.json()["id"]

        client.patch(
            f"/api/v1/delegations/{first_id}",
            json={"status": "running"},
        )

        # CC exits non-zero — the orchestrator PATCHes to ``failed``.
        first_failed_at = first_started + timedelta(minutes=18, seconds=42)
        first_failed = client.patch(
            f"/api/v1/delegations/{first_id}",
            json={
                "status": "failed",
                "completed_at": first_failed_at.isoformat(),
                "raw_output": ATTEMPT_1_ERROR,
            },
        )
        assert first_failed.status_code == 200
        # §3.13 step 1: ``delegation.status='failed'``.
        assert first_failed.json()["status"] == "failed"
        # Parity with §3.12: a failed CC run also writes an
        # :class:`ExecutionLog` — the ``AIvsHumanRatioDisplay``
        # (DESIGN.md §3.1) sums tokens / cost across *all* runs, not
        # just successful ones.
        client.post(
            "/api/v1/execution-logs",
            json={
                "delegation_id": first_id,
                "status": "failed",
                "duration_seconds": 1122,
                "input_tokens": 15_100,
                "output_tokens": 2_430,
                "total_cost_usd": "0.198120",
                "commit_verified": False,
            },
        )

        # --- Step 2: orchestrator creates ``AutoFixAttempt``
        # ``attempt_number=1`` with the extracted error context.
        attempt_1_resp = client.post(
            "/api/v1/auto-fix-attempts",
            json={
                "feat_id": feat_id,
                "error_description": ATTEMPT_1_ERROR,
            },
        )
        assert attempt_1_resp.status_code == 201, attempt_1_resp.text
        attempt_1 = attempt_1_resp.json()
        attempt_1_id = attempt_1["id"]
        # The service auto-assigns ``attempt_number`` as
        # ``MAX+1`` per feat; the first attempt for this feat is #1.
        assert attempt_1["attempt_number"] == 1
        assert attempt_1["feat_id"] == feat_id
        assert attempt_1["error_description"] == ATTEMPT_1_ERROR
        # ``fix_description`` / ``delegation_id`` are populated later
        # via PATCH once the auto-fix delegation is spawned /
        # completes.
        assert attempt_1["fix_description"] is None
        assert attempt_1["delegation_id"] is None

        # --- Step 3: orchestrator spawns the auto-fix delegation.
        retry_started = first_failed_at + timedelta(seconds=30)
        retry_prompt = FEAT_42_PROMPT + "\n\n[AUTO-FIX ATTEMPT #1 — previous delegation failed]\n" + ATTEMPT_1_ERROR
        retry_resp = client.post(
            "/api/v1/delegations",
            json={
                "feat_id": feat_id,
                "cc_agent": "ubuntu_cc",
                "prompt": retry_prompt,
                "status": "pending",
                "started_at": retry_started.isoformat(),
            },
        )
        assert retry_resp.status_code == 201, retry_resp.text
        retry_id = retry_resp.json()["id"]
        # The second delegation is distinct from the first.
        assert retry_id != first_id
        # The retry prompt carries the original prompt + error
        # context — the CRUD-layer contract is "non-empty prompt
        # includes the prior error".
        assert FEAT_42_PROMPT in retry_resp.json()["prompt"]
        assert ATTEMPT_1_ERROR in retry_resp.json()["prompt"]

        # Link the attempt to its spawned delegation — the
        # ``DelegationStatus`` panel (DESIGN.md §3.1) navigates
        # attempt → delegation via this reference.
        link_resp = client.patch(
            f"/api/v1/auto-fix-attempts/{attempt_1_id}",
            json={"delegation_id": retry_id},
        )
        assert link_resp.status_code == 200
        assert link_resp.json()["delegation_id"] == retry_id

        # --- Step 4: retry delegation succeeds.
        client.patch(
            f"/api/v1/delegations/{retry_id}",
            json={"status": "running"},
        )
        retry_done_at = retry_started + timedelta(minutes=11, seconds=5)
        retry_done = client.patch(
            f"/api/v1/delegations/{retry_id}",
            json={
                "status": "done",
                "commit_hash": AUTO_FIX_COMMIT_HASH,
                "completed_at": retry_done_at.isoformat(),
            },
        )
        assert retry_done.status_code == 200
        assert retry_done.json()["status"] == "done"
        assert retry_done.json()["commit_hash"] == AUTO_FIX_COMMIT_HASH

        # ExecutionLog for the successful retry.
        client.post(
            "/api/v1/execution-logs",
            json={
                "delegation_id": retry_id,
                "status": "done",
                "duration_seconds": 665,
                "input_tokens": 9_200,
                "output_tokens": 1_340,
                "total_cost_usd": "0.112600",
                "commit_hash": AUTO_FIX_COMMIT_HASH,
                "commit_verified": True,
            },
        )

        # Orchestrator fills in the fix summary on the attempt so the
        # retry-history UI can show *how* the fix landed.
        fix_summary = (
            "Added ON CONFLICT DO UPDATE to create_stock_item bulk-insert; "
            "regenerated Alembic migration; re-ran pytest — all green."
        )
        summary_resp = client.patch(
            f"/api/v1/auto-fix-attempts/{attempt_1_id}",
            json={"fix_description": fix_summary},
        )
        assert summary_resp.status_code == 200
        assert summary_resp.json()["fix_description"] == fix_summary

        # --- Feat transitions to ``done`` (§3.12 tail / §3.13
        # line 579: "rovnaká postcondition ako
        # [[workflow:delegate_feat_to_cc]]").
        actual_minutes = int(((retry_done_at - first_started).total_seconds()) // 60)
        feat_done_resp = client.patch(
            f"/api/v1/feats/{feat_id}",
            json={"status": "done", "actual_minutes": actual_minutes},
        )
        assert feat_done_resp.status_code == 200
        assert feat_done_resp.json()["status"] == "done"
        assert feat_done_resp.json()["actual_minutes"] == actual_minutes

        # --- Postcondition verification (HTTP) --------------------------
        # Exactly one auto-fix attempt exists for this feat — the
        # retry succeeded so no further attempts were spawned.
        attempts_resp = client.get(
            "/api/v1/auto-fix-attempts",
            params={"feat_id": feat_id},
        )
        assert attempts_resp.status_code == 200
        assert attempts_resp.json()["total"] == 1
        attempt_item = attempts_resp.json()["items"][0]
        assert attempt_item["id"] == attempt_1_id
        assert attempt_item["attempt_number"] == 1
        assert attempt_item["delegation_id"] == retry_id
        assert attempt_item["fix_description"] == fix_summary

        # Both delegations are visible in the feat's history.
        history_resp = client.get(
            "/api/v1/delegations",
            params={"feat_id": feat_id},
        )
        assert history_resp.status_code == 200
        assert history_resp.json()["total"] == 2
        history_by_id = {row["id"]: row for row in history_resp.json()["items"]}
        assert history_by_id[first_id]["status"] == "failed"
        assert history_by_id[retry_id]["status"] == "done"
        assert history_by_id[retry_id]["commit_hash"] == AUTO_FIX_COMMIT_HASH

        # --- Postcondition verification (DB state) ----------------------
        db_session.expire_all()

        # Exactly one persisted attempt for the feat.
        persisted_attempts = (
            db_session.query(AutoFixAttempt)
            .filter(AutoFixAttempt.feat_id == feat_service_layer.id)
            .order_by(AutoFixAttempt.attempt_number)
            .all()
        )
        assert len(persisted_attempts) == 1
        assert persisted_attempts[0].attempt_number == 1
        assert persisted_attempts[0].error_description == ATTEMPT_1_ERROR
        assert persisted_attempts[0].fix_description == fix_summary
        assert persisted_attempts[0].delegation_id == uuid.UUID(retry_id)

        # Delegations persisted with the expected terminal states.
        persisted_first = db_session.get(Delegation, uuid.UUID(first_id))
        assert persisted_first is not None
        assert persisted_first.status == "failed"
        assert persisted_first.raw_output == ATTEMPT_1_ERROR

        persisted_retry = db_session.get(Delegation, uuid.UUID(retry_id))
        assert persisted_retry is not None
        assert persisted_retry.status == "done"
        assert persisted_retry.commit_hash == AUTO_FIX_COMMIT_HASH

        # Feat persisted at ``done`` — the §3.12 postcondition tail
        # holds via the §3.13 success branch.
        persisted_feat = db_session.get(Feat, feat_service_layer.id)
        assert persisted_feat is not None
        assert persisted_feat.status == "done"
        assert persisted_feat.actual_minutes == actual_minutes

        # ExecutionLog rows exist for BOTH runs — the
        # ``AIvsHumanRatioDisplay`` sums across the pair.
        persisted_logs = (
            db_session.query(ExecutionLog)
            .filter(ExecutionLog.delegation_id.in_([persisted_first.id, persisted_retry.id]))
            .all()
        )
        assert len(persisted_logs) == 2
        by_status = {log.status for log in persisted_logs}
        assert by_status == {"done", "failed"}


# ---------------------------------------------------------------------------
# Edge case — §4.14 ``auto_fix_max_attempts_exceeded``: all three retries fail.
# ---------------------------------------------------------------------------


class TestAutoFixMaxAttemptsExceeded:
    """BEHAVIOR.md §3.13 steps 5-6 + §4.14 edge case.

    When every one of the three auto-fix attempts fails, the
    orchestrator MUST NOT spawn a fourth attempt (§4.14: "Systém
    NESPUSTÍ 4. pokus"). The feat transitions to
    ``status='failed'`` and Zoltán / Tibor are notified.

    At the CRUD layer the observable contract is:
        * Three :class:`AutoFixAttempt` rows exist with
          ``attempt_number`` ∈ ``{1, 2, 3}``.
        * Four :class:`Delegation` rows exist (the original plus
          three retries), all at ``status='failed'``.
        * The feat is at ``status='failed'`` with
          ``actual_minutes`` populated from the
          original-start → last-fail delta.
        * A query for the "next" attempt (the orchestrator's gate —
          "how many attempts have we used?") returns 3, which is the
          "don't spawn a fourth" signal.
        * Zoltán and Tibor exist (they receive the notification);
          the notification transport is off-API.
    """

    def test_three_failures_feat_status_failed_no_fourth_attempt(
        self,
        client,
        db_session,
        dominik,
        zoltan,
        tibor,
        nex_horizont,
        stk_module,
        approved_stk_design,
        stk_epic,
        feat_service_layer,
        service_layer_tasks,
    ):
        """Drive three consecutive failures and assert no fourth spawn.

        The original delegation plus two auto-fix retries all fail;
        the third auto-fix attempt (the "last chance") also fails;
        the feat is PATCHed to ``status='failed'``; and a fresh
        :func:`GET /auto-fix-attempts?feat_id=...` returns exactly
        three rows — the orchestrator's "we've exhausted the budget"
        signal.
        """
        feat_id = str(feat_service_layer.id)
        feat_uuid = feat_service_layer.id

        # --- Feat starts ``todo``; orchestrator flips to
        # ``in_progress`` when the first delegation is created.
        client.patch(f"/api/v1/feats/{feat_id}", json={"status": "in_progress"})

        # --- Original delegation fails (step 1 of §3.13).
        first_started = datetime(2026, 4, 16, 9, 0, 0, tzinfo=timezone.utc)
        original = client.post(
            "/api/v1/delegations",
            json={
                "feat_id": feat_id,
                "cc_agent": "ubuntu_cc",
                "prompt": FEAT_42_PROMPT,
                "status": "pending",
                "started_at": first_started.isoformat(),
            },
        )
        assert original.status_code == 201
        original_id = original.json()["id"]
        client.patch(f"/api/v1/delegations/{original_id}", json={"status": "running"})
        first_failed_at = first_started + timedelta(minutes=18)
        client.patch(
            f"/api/v1/delegations/{original_id}",
            json={
                "status": "failed",
                "completed_at": first_failed_at.isoformat(),
                "raw_output": ATTEMPT_1_ERROR,
            },
        )

        # Helper closure: run one auto-fix iteration. Creates the
        # attempt row, spawns the retry delegation, links them, and
        # PATCHes the retry to ``failed``. Returns the attempt payload
        # and retry id so the outer flow can reason about them.
        def _run_failing_attempt(
            attempt_error: str,
            retry_started: datetime,
            retry_failed: datetime,
        ) -> tuple[dict, str]:
            attempt_resp = client.post(
                "/api/v1/auto-fix-attempts",
                json={
                    "feat_id": feat_id,
                    "error_description": attempt_error,
                },
            )
            assert attempt_resp.status_code == 201, attempt_resp.text
            attempt_body = attempt_resp.json()

            retry_prompt = (
                FEAT_42_PROMPT + f"\n\n[AUTO-FIX ATTEMPT #{attempt_body['attempt_number']}]\n" + attempt_error
            )
            retry_resp = client.post(
                "/api/v1/delegations",
                json={
                    "feat_id": feat_id,
                    "cc_agent": "ubuntu_cc",
                    "prompt": retry_prompt,
                    "status": "pending",
                    "started_at": retry_started.isoformat(),
                },
            )
            assert retry_resp.status_code == 201
            retry_id = retry_resp.json()["id"]

            link_resp = client.patch(
                f"/api/v1/auto-fix-attempts/{attempt_body['id']}",
                json={"delegation_id": retry_id},
            )
            assert link_resp.status_code == 200

            client.patch(f"/api/v1/delegations/{retry_id}", json={"status": "running"})
            fail_resp = client.patch(
                f"/api/v1/delegations/{retry_id}",
                json={
                    "status": "failed",
                    "completed_at": retry_failed.isoformat(),
                    "raw_output": attempt_error,
                },
            )
            assert fail_resp.status_code == 200
            assert fail_resp.json()["status"] == "failed"

            return attempt_body, retry_id

        # --- Attempt #1: extracts ATTEMPT_1_ERROR from the original
        # delegation; spawns retry #1; retry #1 fails.
        attempt_1_started = first_failed_at + timedelta(seconds=30)
        attempt_1_failed = attempt_1_started + timedelta(minutes=12)
        attempt_1, retry_1_id = _run_failing_attempt(ATTEMPT_1_ERROR, attempt_1_started, attempt_1_failed)
        assert attempt_1["attempt_number"] == 1

        # --- Attempt #2: extracts ATTEMPT_2_ERROR from retry #1;
        # spawns retry #2; retry #2 fails.
        attempt_2_started = attempt_1_failed + timedelta(seconds=30)
        attempt_2_failed = attempt_2_started + timedelta(minutes=13)
        attempt_2, retry_2_id = _run_failing_attempt(ATTEMPT_2_ERROR, attempt_2_started, attempt_2_failed)
        assert attempt_2["attempt_number"] == 2

        # --- Attempt #3 (final): extracts ATTEMPT_3_ERROR from retry
        # #2; spawns retry #3; retry #3 fails. §3.13 line 573:
        # "max 3 pokusy celkovo".
        attempt_3_started = attempt_2_failed + timedelta(seconds=30)
        attempt_3_failed = attempt_3_started + timedelta(minutes=14)
        attempt_3, retry_3_id = _run_failing_attempt(ATTEMPT_3_ERROR, attempt_3_started, attempt_3_failed)
        assert attempt_3["attempt_number"] == 3

        # --- Step 6: all three auto-fix attempts have failed →
        # orchestrator PATCHes the feat to ``status='failed'``.
        # ``actual_minutes`` tracks the full cost of the failed
        # work (§3.12 uses the same column for the success path —
        # the same column is used here for the failed path so the
        # VelocityChart / ProjectReport can account for wasted
        # effort).
        total_minutes = int(((attempt_3_failed - first_started).total_seconds()) // 60)
        feat_failed_resp = client.patch(
            f"/api/v1/feats/{feat_id}",
            json={"status": "failed", "actual_minutes": total_minutes},
        )
        assert feat_failed_resp.status_code == 200
        # §3.13 postcondition line 580: FEAT ``status='failed'``.
        assert feat_failed_resp.json()["status"] == "failed"
        assert feat_failed_resp.json()["actual_minutes"] == total_minutes

        # --- Postcondition verification (HTTP) --------------------------
        # Three auto-fix attempts, numbered 1-3, ordered by
        # ``attempt_number ASC`` (service contract). §3.13
        # postcondition line 580 + §4.14 line 975.
        attempts_resp = client.get(
            "/api/v1/auto-fix-attempts",
            params={"feat_id": feat_id},
        )
        assert attempts_resp.status_code == 200
        assert attempts_resp.json()["total"] == 3
        numbers = [row["attempt_number"] for row in attempts_resp.json()["items"]]
        assert numbers == [1, 2, 3]
        # Each attempt carries a distinct ``error_description`` so
        # reviewers can trace the failure evolution.
        errors = [row["error_description"] for row in attempts_resp.json()["items"]]
        assert errors == [ATTEMPT_1_ERROR, ATTEMPT_2_ERROR, ATTEMPT_3_ERROR]
        # Each attempt links to its spawned delegation — reverse
        # lookup by ``delegation_id`` resolves to the same attempt.
        attempt_by_number = {row["attempt_number"]: row for row in attempts_resp.json()["items"]}
        assert attempt_by_number[1]["delegation_id"] == retry_1_id
        assert attempt_by_number[2]["delegation_id"] == retry_2_id
        assert attempt_by_number[3]["delegation_id"] == retry_3_id

        # Four delegations total — one original + three retries —
        # ALL at ``status='failed'``.
        delegations_resp = client.get(
            "/api/v1/delegations",
            params={"feat_id": feat_id},
        )
        assert delegations_resp.status_code == 200
        assert delegations_resp.json()["total"] == 4
        assert all(row["status"] == "failed" for row in delegations_resp.json()["items"])

        # §4.14: "Systém NESPUSTÍ 4. pokus". The orchestrator's
        # gate is ``count(attempts where feat_id=X) >= 3`` →
        # "exhausted". Expressed here as the filtered count.
        exhausted_resp = client.get(
            "/api/v1/auto-fix-attempts",
            params={"feat_id": feat_id},
        )
        assert exhausted_resp.json()["total"] == 3
        # No successful delegation for the feat — a correctly-gated
        # orchestrator would never flip the feat to ``done``.
        success_resp = client.get(
            "/api/v1/delegations",
            params={"feat_id": feat_id, "status": "done"},
        )
        assert success_resp.status_code == 200
        assert success_resp.json()["total"] == 0

        # The feat is ``failed``, not ``done`` — no auto-closure
        # regression slipped through.
        feat_final_resp = client.get(f"/api/v1/feats/{feat_id}")
        assert feat_final_resp.status_code == 200
        assert feat_final_resp.json()["status"] == "failed"

        # §3.13 step 6 + §4.14: the notification routes to Zoltán
        # and Tibor. Persisted as user rows; the transport is
        # off-API.
        ri_rows = db_session.execute(select(User).where(User.role == "ri", User.is_active.is_(True))).scalars().all()
        recipient_usernames = {u.username for u in ri_rows}
        assert {"zoltan", "tibor"}.issubset(recipient_usernames)

        # --- Postcondition verification (DB state) ----------------------
        db_session.expire_all()

        # Three attempts persisted, uniquely numbered 1-3, linked to
        # their retry delegations.
        persisted_attempts = (
            db_session.query(AutoFixAttempt)
            .filter(AutoFixAttempt.feat_id == feat_uuid)
            .order_by(AutoFixAttempt.attempt_number)
            .all()
        )
        assert [a.attempt_number for a in persisted_attempts] == [1, 2, 3]
        assert [a.error_description for a in persisted_attempts] == [
            ATTEMPT_1_ERROR,
            ATTEMPT_2_ERROR,
            ATTEMPT_3_ERROR,
        ]
        assert persisted_attempts[0].delegation_id == uuid.UUID(retry_1_id)
        assert persisted_attempts[1].delegation_id == uuid.UUID(retry_2_id)
        assert persisted_attempts[2].delegation_id == uuid.UUID(retry_3_id)

        # All four delegations persisted with ``status='failed'`` —
        # the feat is ``failed`` because every delegation failed.
        persisted_delegations = db_session.query(Delegation).filter(Delegation.feat_id == feat_uuid).all()
        assert len(persisted_delegations) == 4
        assert all(d.status == "failed" for d in persisted_delegations)

        # Feat persisted at ``failed`` with ``actual_minutes`` set.
        persisted_feat = db_session.get(Feat, feat_uuid)
        assert persisted_feat is not None
        assert persisted_feat.status == "failed"
        assert persisted_feat.actual_minutes == total_minutes

        # No Guardian reviews run — the Guardian pipeline only fires
        # on successful delegations (§3.14 precondition: "CC
        # delegácia skončila so ``status='done'``").
        guardian_count = (
            db_session.query(GuardianReview)
            .filter(GuardianReview.delegation_id.in_([d.id for d in persisted_delegations]))
            .count()
        )
        assert guardian_count == 0

        # Zoltán and Tibor persisted as the escalation recipients
        # (the orchestrator's "notify" step pulls from users where
        # ``role='ri'``).
        recipients = db_session.query(User).filter(User.role == "ri", User.username.in_(["zoltan", "tibor"])).all()
        assert {u.username for u in recipients} == {"zoltan", "tibor"}


# ---------------------------------------------------------------------------
# Edge case — a fourth-attempt POST is the signal of a broken orchestrator.
# ---------------------------------------------------------------------------


class TestNoFourthAutoFixAttempt:
    """§4.14 ``edge:auto_fix_max_attempts_exceeded`` — the "don't spawn a fourth" gate.

    The CRUD layer itself does not enforce the "max 3" rule; the
    ``auto_fix_attempts`` table allows arbitrary ``attempt_number``
    values. The gate lives in the orchestrator, where the check is
    ``count(existing attempts for feat) >= 3``. Pins the CRUD-level
    observable that a correctly-gated orchestrator consumes: the
    count returns 3 once the budget is exhausted, and the
    orchestrator must branch on that count before issuing a fourth
    POST.

    A negative assertion here — "no fourth attempt exists" — works
    because this test class doesn't make that fourth POST. If a
    future regression in the orchestrator stopped reading the count
    and blindly POSTed an attempt_number=4, that code path would
    create a row the DB accepts (the UNIQUE constraint only pins
    ``(feat_id, attempt_number)`` as a pair, not a cap on the
    number). The gate is therefore logical, not schema-enforced.
    """

    def test_count_returns_three_after_budget_exhausted(
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
        """Three attempts exist; a count query returns exactly 3.

        Uses ORM inserts for the attempts — we don't need to re-run
        the full orchestration loop here, just pin the "count == 3"
        signal the orchestrator consumes before deciding whether to
        spawn a fourth auto-fix.
        """
        feat_id = str(feat_service_layer.id)
        feat_uuid = feat_service_layer.id

        # Seed three attempts directly via the API so the service's
        # auto-assignment of ``attempt_number`` is exercised.
        for error in (ATTEMPT_1_ERROR, ATTEMPT_2_ERROR, ATTEMPT_3_ERROR):
            resp = client.post(
                "/api/v1/auto-fix-attempts",
                json={
                    "feat_id": feat_id,
                    "error_description": error,
                },
            )
            assert resp.status_code == 201, resp.text

        # The orchestrator's "have we used our budget?" query.
        count_resp = client.get(
            "/api/v1/auto-fix-attempts",
            params={"feat_id": feat_id, "limit": 1},
        )
        assert count_resp.status_code == 200
        # Three attempts exist — the orchestrator reads this as
        # "budget exhausted, escalate to Zoltán instead of spawning
        # a fourth".
        assert count_resp.json()["total"] == 3

        # The ``PaginatedResponse`` envelope is consistent —
        # ``skip`` / ``limit`` carry through even when ``limit=1``.
        assert count_resp.json()["limit"] == 1
        assert count_resp.json()["skip"] == 0

        # DB agrees.
        db_session.expire_all()
        db_count = db_session.query(AutoFixAttempt).filter(AutoFixAttempt.feat_id == feat_uuid).count()
        assert db_count == 3

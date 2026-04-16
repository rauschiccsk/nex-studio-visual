"""Integration test for BEHAVIOR.md §3.16 ``workflow:accept_bug_for_fix``.

Exercises the full happy path of the **accept_bug_for_fix** workflow
end-to-end through the real FastAPI ``app``. §3.16 is the first
director-gated step in the bug lifecycle: after Nazar registers a
bug via §3.15 (``status='new'``) Zoltán — or Tibor, both
``role='ri'`` — opens the bug, clicks "Akceptovať" to move it into
``status='accepted'``, and then attaches one or more
:class:`~backend.db.models.bugs.BugFixTask` rows to scope the work
Dominik will later delegate to Ubuntu CC via §3.17
``workflow:delegate_bug_fix``.

The worked example in §3.16 steps 1-3 is:

    * **Step 1** — Zoltán opens BUG-012 → clicks "Akceptovať" →
      ``status='accepted'``.
    * **Step 2** — Zoltán clicks "Pridať fix task" → UI-only form
      opens.
    * **Step 3** — Zoltán fills in two fix tasks:
        1. *"Fix phone field null handling in PAB service"* —
           ``task_type='backend'``.
        2. *"Add empty phone test"* — ``task_type='test'``.
      The system creates ``BUG-012/FIX-1`` and ``BUG-012/FIX-2``.
    * **Step 4** — (system) — bug stays at ``accepted``, waiting
      for Dominik's §3.17 delegation.

§3.16 step 3 references "BUG-012/FIX-1" and "BUG-012/FIX-2"
explicitly — the ``number`` column on :class:`BugFixTask` is
scoped per bug (``UNIQUE(bug_id, number)``) and auto-assigned as
``MAX(number) + 1`` by the service layer (:mod:`backend.services.bug_fix_task`
:func:`_next_number`). The first fix task attached to any bug is
always ``FIX-1``.

    Precondition (per BEHAVIOR.md §3.16, lines 656-658):
        * :class:`Bug` exists with ``status='new'`` — the §3.15
          postcondition. Nazar already filed BUG-012; the fixture
          graph seeds it directly with ``status='new'`` and
          ``bug_number=12`` so the "accept the new bug" step has
          something to land on.
        * Actor has ``role='ri'``. §3.16 names Zoltán
          (``ri_director``) and Tibor (``ri_senior``) as the two
          valid actors. Role enforcement is an auth-middleware
          concern (the router layer does not wire a JWT dependency
          yet); the test still persists both Zoltán and Tibor as
          ``role='ri'`` users so the "recipient lookup" contract —
          "all project members with ``role='ri'`` may accept" — is
          observable.

    Steps (per BEHAVIOR.md §3.16, lines 663-667):
        1. Zoltán opens BUG-012 → clicks "Akceptovať" →
           ``PATCH /api/v1/bugs/{id}`` with ``{"status": "accepted"}``.
           The service updates the row, bumps ``updated_at`` via
           ``onupdate=func.now()`` and returns HTTP 200 with the new
           status. ``resolved_at`` stays ``None`` — the auto-stamp
           on :mod:`backend.services.bug` :func:`update` only fires
           on the ``→ resolved`` transition.
        2. Zoltán clicks "Pridať fix task". Client-side only — no
           HTTP round-trip.
        3. Zoltán submits the two fix tasks →
           ``POST /api/v1/bug-fix-tasks`` twice. The service
           auto-assigns ``number=1`` for the first POST and
           ``number=2`` for the second (``MAX(number) + 1`` per
           bug); ``status`` defaults to ``'todo'`` via
           ``server_default``; HTTP 201 each time.
        4. — (system) — bug status stays at ``accepted``. The
           §3.17 ``delegate_bug_fix`` workflow is the one that
           transitions to ``in_progress`` when Dominik picks up
           the first fix task. Observable here as: BUG-012 still
           at ``accepted`` after both fix tasks land; the
           ``status='new'`` filter no longer returns it; the
           ``status='accepted'`` filter does.

    Postcondition (per BEHAVIOR.md §3.16, lines 669-672):
        * The bug is at ``status='accepted'``.
        * One or more :class:`BugFixTask` rows are linked to the
          bug via ``bug_id`` FK — the worked example attaches two
          (``FIX-1`` backend, ``FIX-2`` test).
        * Dominik can see BUG-012 with its fix tasks and may
          delegate them — modelled here as the list query
          ``GET /api/v1/bug-fix-tasks?bug_id=...`` returning both
          rows and the ``status='todo'`` filter (Dominik's
          "inbox" for §3.17 delegation) returning both.

Edge cases verified alongside the happy path:

    * **Both ``ri`` directors may accept** — Tibor (``ri_senior``)
      accepts a second "new" bug and attaches a fix task. §3.16
      lists both ``ri`` roles as valid actors; pinning Tibor's
      path prevents an accidental narrowing to "Zoltán only" when
      auth is eventually wired.
    * **``FIX-number`` is scoped per bug** — two sibling bugs in
      the same project each start their own fix-task numbering at
      ``1``. Pins DESIGN.md §1.17 "``UNIQUE(bug_id, number)``"
      and the service-layer :func:`_next_number` scope.
    * **``FIX-number`` auto-increments** — a third fix task on
      BUG-012 lands at ``number=3`` without the client sending
      ``number``. Pins the ``MAX(number) + 1`` formula across
      consecutive POSTs.
    * **Client-supplied ``number`` is ignored** — the
      :class:`BugFixTaskCreate` schema does not declare ``number``.
      A payload that tries to set ``number=999`` is silently
      dropped (Pydantic's default extra-field policy) and the
      service still assigns the next natural value. This pins
      the contract against a future schema change that
      accidentally opens the field.
    * **Empty ``title``** — §3.16 step 2 names "Názov" as a
      mandatory form field; :class:`BugFixTaskCreate.title` has
      ``min_length=1``, so an empty string → HTTP 422. Nothing
      is written.
    * **Invalid ``task_type``** — the CHECK constraint
      ``ck_bug_fix_tasks_task_type`` pins
      ``task_type IN ('backend', 'frontend', 'migration', 'test',
      'docs')``; the Pydantic ``BugFixTaskType`` literal mirrors
      it. A payload with ``task_type='devops'`` → HTTP 422 at the
      schema layer.
    * **Accept a missing bug → HTTP 404** — PATCH against a
      random UUID that does not resolve to any row surfaces as
      HTTP 404 (service :class:`ValueError` "not found" → 404
      via the router's error mapper). Nothing is written.

Auth note:
    Same as the rest of the Feat 7 integration tests — the router
    layer does not wire a JWT dependency yet, so the "Actor has
    role=ri" precondition is satisfied by persisting the actor
    with the correct role. Role enforcement is a separate
    auth-middleware concern; the test focuses on the CRUD-layer
    contract the frontend hits.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.db.models.bugs import Bug, BugFixTask
from backend.db.models.foundation import User
from backend.db.models.projects import Project, ProjectMember

# ---------------------------------------------------------------------------
# Precondition fixtures — NEX Horizont project with Zoltán (ri_director),
# Tibor (ri_senior), Nazar (shu_junior reporter) and Dominik (ha_medior
# future delegator) as members. BUG-012 is pre-seeded at ``status='new'``
# so §3.16 has a real bug to accept — the §3.15 register_bug workflow is
# assumed to have already run.
# ---------------------------------------------------------------------------


@pytest.fixture()
def zoltan(db_session) -> User:
    """Persist Zoltán — the ``ri_director`` actor from §3.16.

    §3.16 names Zoltán as the primary actor (line 664 "Zoltán
    otvorí BUG-012 → klikne 'Akceptovať'"). Role is ``ri``; the
    role is the observable precondition for the §3.16 actor
    check.
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
    """Persist Tibor — the ``ri_senior`` alternative actor from §3.16.

    §3.16 lists both ``ri_director`` and ``ri_senior`` as valid
    actors (line 652). Tibor's fixture lets the companion "both
    directors can accept" test flip the actor without
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
def nazar(db_session) -> User:
    """Persist Nazar — the ``shu_junior`` who filed BUG-012 via §3.15.

    Nazar is the ``created_by`` for BUG-012 — §3.15 worked
    example. He plays no role in §3.16 itself (the bug is
    already filed), but the FK ``bugs.created_by`` is NOT NULL
    so the fixture graph needs him.
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
def dominik(db_session) -> User:
    """Persist Dominik — the ``ha_medior`` future delegator.

    §3.16 postcondition line 672: "Dominik vidí bug s fix tasks
    a môže ich delegovať". Dominik is the §3.17 actor; his
    visibility is asserted here via the CRUD-layer list queries
    (he would hit the same endpoints from the bugs page). Seeded
    so the project-members table has him attached.
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
def nex_horizont(db_session, zoltan, tibor, nazar, dominik) -> Project:
    """Persist the NEX Horizont project with all four actors as members.

    Matches the §3.15 / §3.16 worked example: BUG-012 is filed
    against NEX Horizont. All four users are added to the
    ``project_members`` join table so the "is member of project"
    implicit precondition is satisfied for every actor.
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

    for user in (zoltan, tibor, nazar, dominik):
        db_session.add(ProjectMember(project_id=project.id, user_id=user.id))
    db_session.flush()
    return project


@pytest.fixture()
def bug_012(db_session, nex_horizont, nazar) -> Bug:
    """Seed BUG-012 at ``status='new'`` — the §3.16 precondition.

    BEHAVIOR.md §3.16 precondition line 657: ":class:`bugs`
    existuje so ``status='new'``". The §3.15 register_bug test
    produces this row; §3.16 only cares that it already exists.
    Seeded directly to keep this test focused on the accept /
    fix-task workflow rather than re-testing §3.15 in the same
    file.

    ``bug_number=12`` reproduces the "BUG-012" label from
    §3.16 steps 1 and 3 faithfully. ``created_by=nazar.id``
    mirrors §3.15 step 3 — Nazar is the reporter.
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
        status="new",
        source="internal",
        environment="development",
        created_by=nazar.id,
    )
    db_session.add(bug)
    db_session.flush()
    return bug


# ---------------------------------------------------------------------------
# Helpers — build PATCH and POST payloads that mirror the §3.16 worked
# example.
# ---------------------------------------------------------------------------


# BEHAVIOR.md §3.16 step 3 — the two worked-example fix tasks.
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


def _fix_task_payload(bug_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    """Build a JSON payload for ``POST /api/v1/bug-fix-tasks``.

    Defaults mirror the §3.16 step 3 first fix task exactly.
    Individual tests override ``title``, ``task_type`` and
    ``description`` as needed.
    """
    payload: dict[str, Any] = {
        "bug_id": str(bug_id),
        "title": FIX_1_TITLE,
        "description": FIX_1_DESCRIPTION,
        "task_type": FIX_1_TASK_TYPE,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.16 end-to-end, BUG-012 + FIX-1/FIX-2.
# ---------------------------------------------------------------------------


class TestAcceptBugForFixHappyPath:
    """End-to-end walkthrough of workflow §3.16 against the real app."""

    def test_full_workflow_accepts_bug_012_and_creates_two_fix_tasks(
        self,
        client,
        db_session,
        zoltan,
        dominik,
        nex_horizont,
        bug_012,
    ):
        """Drive steps 1-4 of the workflow and verify every postcondition.

        The worked example from BEHAVIOR.md §3.16 is reproduced
        faithfully: Zoltán (``ri_director``) accepts BUG-012,
        then attaches two fix tasks — FIX-1 (backend, phone null
        handling) and FIX-2 (test, empty-phone regression). The
        bug stays at ``status='accepted'``; both fix tasks land
        at ``status='todo'``; Dominik's CRUD-layer view (the
        ``status='todo'`` fix-task list) returns both rows.
        """
        # --- Step 1: Zoltán opens BUG-012. The bug-detail endpoint
        # returns the seeded ``status='new'`` row. The UI renders the
        # "Akceptovať" button because ``status`` is ``'new'`` (the
        # button is hidden for any other status).
        initial = client.get(f"/api/v1/bugs/{bug_012.id}")
        assert initial.status_code == 200, initial.text
        assert initial.json()["status"] == "new"
        assert initial.json()["bug_number"] == 12
        assert initial.json()["resolved_at"] is None

        # And the "Čaká na akceptáciu" inbox for the ``ri`` users
        # — ``GET /api/v1/bugs?status=new`` — returns BUG-012 as
        # the only new bug in NEX Horizont.
        new_inbox = client.get(
            "/api/v1/bugs",
            params={"project_id": str(nex_horizont.id), "status": "new"},
        )
        assert new_inbox.status_code == 200
        assert new_inbox.json()["total"] == 1
        assert new_inbox.json()["items"][0]["id"] == str(bug_012.id)

        # --- Step 1 (cont.): Zoltán clicks "Akceptovať". The UI sends
        # ``PATCH /api/v1/bugs/{id}`` with ``{"status": "accepted"}``
        # — the minimum viable payload: the bug schema is PATCH, so
        # only the fields the client wants to change are included.
        # The service updates ``status``, bumps ``updated_at`` via
        # ``onupdate=func.now()`` and returns HTTP 200.
        accept_resp = client.patch(
            f"/api/v1/bugs/{bug_012.id}",
            json={"status": "accepted"},
        )
        assert accept_resp.status_code == 200, accept_resp.text
        accepted = accept_resp.json()
        # §3.16 step 1 system response: ``status='accepted'``.
        assert accepted["status"] == "accepted"
        # §3.16 postcondition line 670 — all immutable fields
        # preserved, identity intact.
        assert accepted["id"] == str(bug_012.id)
        assert accepted["bug_number"] == 12
        assert accepted["project_id"] == str(nex_horizont.id)
        assert accepted["title"] == bug_012.title
        assert accepted["description"] == bug_012.description
        assert accepted["severity"] == "major"
        assert accepted["source"] == "internal"
        assert accepted["environment"] == "development"
        # ``resolved_at`` stays ``None`` — the service only
        # auto-stamps it on the ``→ resolved`` transition, not on
        # ``→ accepted``. BUG-012 has not been resolved yet.
        assert accepted["resolved_at"] is None
        assert accepted["commit_hash"] is None

        # --- Step 2: Zoltán clicks "Pridať fix task". UI-only —
        # opens the "Názov / Typ / Popis" form. No HTTP round-trip.

        # --- Step 3a: Zoltán submits FIX-1 — backend task.
        # ``number`` is auto-assigned by the service as
        # ``MAX(number) + 1`` for the bug (the bug has no fix tasks
        # yet, so ``number`` lands at ``1``). ``status`` defaults to
        # ``'todo'`` via ``server_default``. HTTP 201.
        fix1_resp = client.post(
            "/api/v1/bug-fix-tasks",
            json=_fix_task_payload(bug_012.id),
        )
        assert fix1_resp.status_code == 201, fix1_resp.text
        fix1 = fix1_resp.json()
        # §3.16 step 3 system response: "BUG-012/FIX-1".
        assert fix1["number"] == 1
        assert fix1["status"] == "todo"
        assert fix1["bug_id"] == str(bug_012.id)
        assert fix1["title"] == FIX_1_TITLE
        assert fix1["description"] == FIX_1_DESCRIPTION
        assert fix1["task_type"] == FIX_1_TASK_TYPE
        # Estimate / actual minutes are optional — unset on
        # creation, populated later by the delegation workflow.
        assert fix1["estimated_minutes"] is None
        assert fix1["actual_minutes"] is None
        assert fix1["checklist_type"] is None
        # Server-generated identity columns are populated.
        assert fix1["id"]
        assert fix1["created_at"]
        assert fix1["updated_at"]
        fix1_id = fix1["id"]

        # --- Step 3b: Zoltán submits FIX-2 — test task. The
        # service now sees ``MAX(number) = 1`` for the bug, so
        # ``number`` auto-assigns to ``2``. ``status`` defaults to
        # ``'todo'`` again. HTTP 201.
        fix2_resp = client.post(
            "/api/v1/bug-fix-tasks",
            json=_fix_task_payload(
                bug_012.id,
                title=FIX_2_TITLE,
                description=FIX_2_DESCRIPTION,
                task_type=FIX_2_TASK_TYPE,
            ),
        )
        assert fix2_resp.status_code == 201, fix2_resp.text
        fix2 = fix2_resp.json()
        # §3.16 step 3 system response: "BUG-012/FIX-2".
        assert fix2["number"] == 2
        assert fix2["status"] == "todo"
        assert fix2["bug_id"] == str(bug_012.id)
        assert fix2["title"] == FIX_2_TITLE
        assert fix2["description"] == FIX_2_DESCRIPTION
        assert fix2["task_type"] == FIX_2_TASK_TYPE
        fix2_id = fix2["id"]
        # FIX-1 and FIX-2 are distinct rows with distinct ids.
        assert fix1_id != fix2_id

        # --- Step 4 (system response): bug status stays
        # ``accepted`` — the §3.17 ``delegate_bug_fix`` workflow
        # is the one that transitions to ``in_progress``. Assert
        # the bug has not drifted.
        after_accept = client.get(f"/api/v1/bugs/{bug_012.id}")
        assert after_accept.status_code == 200
        assert after_accept.json()["status"] == "accepted"

        # --- Postcondition verification (HTTP) -------------------------
        # 1. §3.16 postcondition line 670: bug at ``status='accepted'``.
        #    Already asserted via ``accept_resp``; re-read here to
        #    confirm persistence through the router layer.
        show_bug = client.get(f"/api/v1/bugs/{bug_012.id}")
        assert show_bug.status_code == 200
        assert show_bug.json()["status"] == "accepted"
        # BUG-012 no longer appears in the "new" inbox — it has
        # been accepted out.
        new_inbox_after = client.get(
            "/api/v1/bugs",
            params={"project_id": str(nex_horizont.id), "status": "new"},
        )
        assert new_inbox_after.status_code == 200
        assert new_inbox_after.json()["total"] == 0
        # And appears in the "accepted" filter exactly once.
        accepted_inbox = client.get(
            "/api/v1/bugs",
            params={"project_id": str(nex_horizont.id), "status": "accepted"},
        )
        assert accepted_inbox.status_code == 200
        assert accepted_inbox.json()["total"] == 1
        assert accepted_inbox.json()["items"][0]["id"] == str(bug_012.id)

        # 2. §3.16 postcondition line 671: one-or-more
        #    :class:`BugFixTask` rows linked to the bug. The
        #    bug-detail page's fix-task list is
        #    ``GET /api/v1/bug-fix-tasks?bug_id=...``; it returns
        #    both rows ordered by ``number``.
        fix_list = client.get(
            "/api/v1/bug-fix-tasks",
            params={"bug_id": str(bug_012.id)},
        )
        assert fix_list.status_code == 200
        assert fix_list.json()["total"] == 2
        numbers = [row["number"] for row in fix_list.json()["items"]]
        # ``list_bug_fix_tasks`` orders by ``(bug_id, number)``,
        # so the two rows come back in ``FIX-1, FIX-2`` order.
        assert numbers == [1, 2]
        assert fix_list.json()["items"][0]["id"] == fix1_id
        assert fix_list.json()["items"][1]["id"] == fix2_id

        # 3. §3.16 postcondition line 672: Dominik sees the bug
        #    with its fix tasks and may delegate them. Dominik's
        #    "ready to delegate" inbox is the ``status='todo'``
        #    fix-task list — both FIX-1 and FIX-2 are fresh so
        #    both land there.
        todo_inbox = client.get(
            "/api/v1/bug-fix-tasks",
            params={"bug_id": str(bug_012.id), "status": "todo"},
        )
        assert todo_inbox.status_code == 200
        assert todo_inbox.json()["total"] == 2
        # Narrowed to ``task_type='backend'`` — only FIX-1 comes
        # back. Pins the filter that the bugs-page chip filter
        # relies on.
        backend_tasks = client.get(
            "/api/v1/bug-fix-tasks",
            params={"bug_id": str(bug_012.id), "task_type": "backend"},
        )
        assert backend_tasks.status_code == 200
        assert backend_tasks.json()["total"] == 1
        assert backend_tasks.json()["items"][0]["id"] == fix1_id
        # And ``task_type='test'`` — only FIX-2.
        test_tasks = client.get(
            "/api/v1/bug-fix-tasks",
            params={"bug_id": str(bug_012.id), "task_type": "test"},
        )
        assert test_tasks.status_code == 200
        assert test_tasks.json()["total"] == 1
        assert test_tasks.json()["items"][0]["id"] == fix2_id

        # 4. The "Dominik's project view" query — all members with
        #    ``role='ha'`` — returns Dominik. This is the
        #    recipient-lookup contract for the §3.17 notification
        #    the orchestrator will send once the fix tasks are
        #    ready to delegate.
        ha_users = client.get("/api/v1/users", params={"role": "ha"})
        assert ha_users.status_code == 200
        ha_usernames = {row["username"] for row in ha_users.json()["items"]}
        assert "dominik" in ha_usernames

        # --- Postcondition verification (DB state) ---------------------
        db_session.expire_all()

        # §3.16 postcondition line 670: bug ``status='accepted'``.
        persisted_bug = db_session.get(Bug, bug_012.id)
        assert persisted_bug is not None
        assert persisted_bug.status == "accepted"
        assert persisted_bug.bug_number == 12
        # Immutable audit fields preserved.
        assert persisted_bug.created_by == bug_012.created_by
        assert persisted_bug.project_id == nex_horizont.id
        # No resolution side-effects on the ``→ accepted``
        # transition.
        assert persisted_bug.resolved_at is None
        assert persisted_bug.commit_hash is None

        # §3.16 postcondition line 671: both fix tasks live in
        # the DB, linked to the bug, ``status='todo'``, numbered
        # 1 and 2.
        persisted_fix1 = db_session.get(BugFixTask, uuid.UUID(fix1_id))
        persisted_fix2 = db_session.get(BugFixTask, uuid.UUID(fix2_id))
        assert persisted_fix1 is not None
        assert persisted_fix2 is not None
        assert persisted_fix1.bug_id == bug_012.id
        assert persisted_fix2.bug_id == bug_012.id
        assert persisted_fix1.number == 1
        assert persisted_fix2.number == 2
        assert persisted_fix1.status == "todo"
        assert persisted_fix2.status == "todo"
        assert persisted_fix1.task_type == "backend"
        assert persisted_fix2.task_type == "test"
        assert persisted_fix1.title == FIX_1_TITLE
        assert persisted_fix2.title == FIX_2_TITLE

        # The UNIQUE(bug_id, number) constraint guarantees the
        # two rows own distinct numbers — assert the full set
        # materialises in the DB.
        all_fixes = (
            db_session.query(BugFixTask).filter(BugFixTask.bug_id == bug_012.id).order_by(BugFixTask.number).all()
        )
        assert [row.number for row in all_fixes] == [1, 2]

    def test_tibor_ri_senior_may_also_accept_a_new_bug(
        self,
        client,
        db_session,
        tibor,
        nazar,
        nex_horizont,
    ):
        """BEHAVIOR.md §3.16 actor line — both ``ri`` users are valid.

        §3.16 lists ``ri_director`` and ``ri_senior`` as the two
        valid actors. Zoltán (``ri_director``) is covered by the
        worked example; Tibor (``ri_senior``) is pinned here so
        the contract is not accidentally narrowed to one director.
        A second bug (BUG-050) is seeded and accepted by Tibor; a
        single fix task is attached — the postcondition line 671
        says "1+ záznamy", so one is enough.
        """
        # Seed a second "new" bug so Tibor has something to accept.
        # BUG-050 is chosen to make it clearly distinct from BUG-012
        # in the worked example.
        second_bug = Bug(
            project_id=nex_horizont.id,
            bug_number=50,
            title="Migration dry-run crashes on empty source table.",
            description="Steps: run MIG dry-run against a fresh DB...",
            severity="minor",
            status="new",
            source="internal",
            environment="development",
            created_by=nazar.id,
        )
        db_session.add(second_bug)
        db_session.flush()

        # Tibor accepts.
        accept_resp = client.patch(
            f"/api/v1/bugs/{second_bug.id}",
            json={"status": "accepted"},
        )
        assert accept_resp.status_code == 200, accept_resp.text
        assert accept_resp.json()["status"] == "accepted"

        # Tibor attaches a single fix task — the postcondition
        # requires "1+ záznamy", so one satisfies §3.16.
        fix_resp = client.post(
            "/api/v1/bug-fix-tasks",
            json=_fix_task_payload(
                second_bug.id,
                title="Guard empty-table branch in MIG dry-run runner",
                task_type="migration",
                description="Skip INSERT phase when source count == 0; emit summary row instead.",
            ),
        )
        assert fix_resp.status_code == 201, fix_resp.text
        assert fix_resp.json()["number"] == 1
        assert fix_resp.json()["status"] == "todo"
        assert fix_resp.json()["task_type"] == "migration"

        # DB state — bug accepted, one fix task linked, tibor is
        # ``ri`` (recipient-lookup precondition).
        db_session.expire_all()
        persisted = db_session.get(Bug, second_bug.id)
        assert persisted is not None
        assert persisted.status == "accepted"
        fix_count = db_session.query(BugFixTask).filter(BugFixTask.bug_id == second_bug.id).count()
        assert fix_count == 1
        assert tibor.role == "ri"


# ---------------------------------------------------------------------------
# Edge cases — fix-task numbering scope, auto-increment, form validation
# and the accept-missing-bug 404.
# ---------------------------------------------------------------------------


class TestAcceptBugForFixEdgeCases:
    """Numbering, form validation and missing-bug contracts.

    §3.16 pins three invariants on the fix-task axis — per-bug
    scoping of ``number`` (DESIGN.md §1.17 ``UNIQUE(bug_id,
    number)``), auto-increment via :func:`_next_number`, and
    the two client-side form validations (mandatory ``title``
    and the ``task_type`` CHECK). The edge cases below exercise
    each one plus the PATCH-against-missing-bug 404 path.
    """

    def test_fix_number_is_scoped_per_bug(
        self,
        client,
        db_session,
        nazar,
        nex_horizont,
        bug_012,
    ):
        """Two sibling bugs each start their own fix-task numbering at 1.

        DESIGN.md §1.17 / the DB ``UNIQUE(bug_id, number)``
        constraint (``uq_bug_fix_tasks_bug_id_number``) is scoped
        *per bug*. BUG-012 already owns FIX-1 after its first
        fix-task POST; a fresh sibling bug (BUG-013) therefore
        starts at FIX-1 on its own first fix-task POST — not at
        FIX-2. This is the exact contract the UI's "BUG-xxx /
        FIX-y" label relies on.
        """
        # Seed BUG-013 as a sibling bug in the same project.
        bug_013 = Bug(
            project_id=nex_horizont.id,
            bug_number=13,
            title="Projects page filter drops the search term on reload.",
            description="Steps: open projects page, filter by 'foo', reload...",
            severity="minor",
            status="accepted",
            source="internal",
            environment="development",
            created_by=nazar.id,
        )
        db_session.add(bug_013)
        db_session.flush()

        # FIX-1 on BUG-012 — first fix task for the bug.
        fix_012 = client.post(
            "/api/v1/bug-fix-tasks",
            json=_fix_task_payload(bug_012.id),
        )
        assert fix_012.status_code == 201, fix_012.text
        assert fix_012.json()["number"] == 1
        assert fix_012.json()["bug_id"] == str(bug_012.id)

        # FIX-1 on BUG-013 — NOT FIX-2. Per-bug scope.
        fix_013 = client.post(
            "/api/v1/bug-fix-tasks",
            json=_fix_task_payload(
                bug_013.id,
                title="Persist projects-page filter in URL query string",
                task_type="frontend",
            ),
        )
        assert fix_013.status_code == 201, fix_013.text
        assert fix_013.json()["number"] == 1
        assert fix_013.json()["bug_id"] == str(bug_013.id)

        # Both rows coexist; the ``(bug_id, number)`` pair is
        # unique on each side.
        db_session.expire_all()
        persisted_012 = db_session.get(BugFixTask, uuid.UUID(fix_012.json()["id"]))
        persisted_013 = db_session.get(BugFixTask, uuid.UUID(fix_013.json()["id"]))
        assert persisted_012 is not None
        assert persisted_013 is not None
        assert persisted_012.number == 1
        assert persisted_013.number == 1
        assert persisted_012.bug_id != persisted_013.bug_id

    def test_fix_number_auto_increments_across_consecutive_fix_tasks(
        self,
        client,
        db_session,
        bug_012,
    ):
        """Consecutive fix-task POSTs → FIX-1, FIX-2, FIX-3 in order.

        Pins the service-layer ``MAX(number) + 1`` formula across
        multiple sequential POSTs against the same bug. Three
        fix tasks attached to a freshly-accepted bug must produce
        1, 2, 3 with no gaps.
        """
        numbers: list[int] = []
        for idx in range(3):
            resp = client.post(
                "/api/v1/bug-fix-tasks",
                json=_fix_task_payload(
                    bug_012.id,
                    title=f"Sequential fix task #{idx}",
                ),
            )
            assert resp.status_code == 201, resp.text
            numbers.append(resp.json()["number"])

        assert numbers == [1, 2, 3]

        # DB confirms the three rows all live under BUG-012 with
        # consecutive numbers.
        db_session.expire_all()
        all_numbers = sorted(
            row.number for row in db_session.query(BugFixTask).filter(BugFixTask.bug_id == bug_012.id).all()
        )
        assert all_numbers == [1, 2, 3]

    def test_client_supplied_number_is_ignored_server_assigns(
        self,
        client,
        db_session,
        bug_012,
    ):
        """Client-sent ``number`` must not override the service.

        :class:`BugFixTaskCreate` does not declare ``number`` as
        a field; Pydantic's default extra-field policy silently
        drops it. The test pins that contract — a malicious or
        confused client cannot skip the queue by sending
        ``number=999``; the server still assigns the next
        natural value (``1`` for a freshly-accepted bug).
        """
        resp = client.post(
            "/api/v1/bug-fix-tasks",
            json={
                **_fix_task_payload(bug_012.id),
                "number": 999,
            },
        )
        assert resp.status_code == 201, resp.text
        # Server-side assignment wins.
        assert resp.json()["number"] == 1

        db_session.expire_all()
        persisted = db_session.get(BugFixTask, uuid.UUID(resp.json()["id"]))
        assert persisted is not None
        assert persisted.number == 1

    def test_empty_fix_task_title_is_rejected_with_422(
        self,
        client,
        db_session,
        bug_012,
    ):
        """Empty ``title`` → HTTP 422, nothing written.

        §3.16 step 2 names "Názov" as a mandatory form field;
        :class:`BugFixTaskCreate.title` has ``min_length=1``, so
        the UI cannot submit the form without it and the API
        schema's ``min_length=1`` is the server-side mirror of
        that constraint. A payload with ``title=""`` must be
        rejected at the Pydantic layer — HTTP 422 with no row
        written.
        """
        resp = client.post(
            "/api/v1/bug-fix-tasks",
            json=_fix_task_payload(bug_012.id, title=""),
        )
        assert resp.status_code == 422, resp.text

        # No fix tasks were written — the count is still 0.
        db_session.expire_all()
        total = db_session.query(BugFixTask).filter(BugFixTask.bug_id == bug_012.id).count()
        assert total == 0

    def test_invalid_fix_task_type_is_rejected_with_422(
        self,
        client,
        db_session,
        bug_012,
    ):
        """``task_type`` outside the allowed set → HTTP 422.

        The DB CHECK constraint ``ck_bug_fix_tasks_task_type``
        pins the allowed set (``backend | frontend | migration |
        test | docs``); the :data:`BugFixTaskType` Pydantic
        literal mirrors it. A payload with ``task_type='devops'``
        is rejected at the schema layer before any DB touch —
        HTTP 422 with no row written. This pins the form's "Typ"
        dropdown contract from §3.16 step 2.
        """
        resp = client.post(
            "/api/v1/bug-fix-tasks",
            json=_fix_task_payload(bug_012.id, task_type="devops"),
        )
        assert resp.status_code == 422, resp.text

        db_session.expire_all()
        total = db_session.query(BugFixTask).filter(BugFixTask.bug_id == bug_012.id).count()
        assert total == 0

    def test_accept_missing_bug_returns_404(
        self,
        client,
        db_session,
        nex_horizont,
    ):
        """``PATCH /api/v1/bugs/{random_uuid}`` → HTTP 404.

        §3.16 precondition line 657 — the bug must exist. The
        UI can only reach §3.16 step 1 after §3.15 has filed the
        bug, but a direct PATCH against a random UUID (e.g. a
        stale tab after the bug was deleted) is not a contract
        the CRUD layer enforces at the precondition level. It
        surfaces instead as the router's ``not found``
        :class:`ValueError` → HTTP 404 mapping. Nothing is
        written.
        """
        phantom_id = uuid.uuid4()
        resp = client.patch(
            f"/api/v1/bugs/{phantom_id}",
            json={"status": "accepted"},
        )
        assert resp.status_code == 404, resp.text

        # No bug with the phantom id exists — assert it via the
        # DB session directly. Belt-and-braces check that a 404
        # did not accidentally create a row.
        db_session.expire_all()
        assert db_session.get(Bug, phantom_id) is None

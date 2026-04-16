"""Integration test for BEHAVIOR.md §3.15 ``workflow:register_bug``.

Exercises the full happy path of the **register_bug** workflow
end-to-end through the real FastAPI ``app``. §3.15 is the
"file a bug" entry point — any member of the project (Nazar
``shu_junior`` in the worked example, but equally Dominik
``ha_medior`` or the two ``ri`` users) opens the "Bugy" tab and
registers a new defect against a project. The workflow is
intentionally low-friction: a short form with ``Názov``, ``Popis``,
``Závažnosť``, ``Zdroj`` and ``Prostredie`` lands a single row in
the :class:`Bug` table with ``status='new'`` and an auto-assigned
``bug_number``.

The worked example in §3.15 step 2 is:

    * ``Názov``        = ``"PAB detail page crashes on empty phone field"``
    * ``Závažnosť``    = ``"major"``
    * ``Zdroj``        = ``"internal"``
    * ``Prostredie``   = ``"development"``
    * kroky reprodukcie → body of the ``Popis`` textarea.

§3.15 step 4 references "BUG-012" — the 12th bug in the project.
NEX Horizont has been in flight long enough that eleven prior bugs
exist; the test seeds those eleven rows so ``bug_number`` reaches
``12`` naturally (the service computes it as ``MAX(bug_number) +
1`` per project — :mod:`backend.services.bug` :func:`_next_bug_number`).

    Precondition (per BEHAVIOR.md §3.15, lines 627-629):
        * The actor is persisted and is a member of the project
          (``project_members`` row). Nazar is the worked-example
          actor (``role='shu'``); the §3.15 actor line also lists
          ``ha_medior``, ``ri_director`` and ``ri_senior`` as
          equally valid — register_bug is the lowest-friction
          workflow in NEX Studio.
        * The project exists.

    Steps (per BEHAVIOR.md §3.15, lines 633-638):
        1. Nazar opens NEX Horizont → tab "Bugy" → clicks "Nový
           bug". UI-only — modelled here as
           ``GET /api/v1/bugs?project_id=...`` which returns the
           eleven pre-existing rows so the bugs-page render is
           exercised.
        2. Nazar fills in the form — client-side only, no HTTP
           round-trip.
        3. Nazar clicks "Zaregistrovať bug" →
           ``POST /api/v1/bugs`` returns HTTP 201 with the
           persisted row. ``bug_number`` is auto-assigned
           (``12`` in the worked example), ``status`` defaults to
           ``'new'`` (``server_default``) and ``created_by`` is
           Nazar's id.
        4. — (system) — the confirmation string "Bug BUG-012 bol
           zaregistrovaný. Čaká na akceptáciu." is a UI-only label
           rendered from ``bug.bug_number`` and ``bug.status``.
           Not observable at the CRUD layer; the test asserts the
           two source fields instead.

    Postcondition (per BEHAVIOR.md §3.15, lines 640-643):
        * ``bugs`` row exists with ``status='new'`` and a
          ``bug_number`` unique per project.
        * The row is visible to every project member — modelled
          here as the list query ``GET /api/v1/bugs?project_id=...``
          returning the new row alongside the eleven seeded ones.
        * Notification to Zoltán and Tibor ("Nový bug BUG-012") —
          the notification-delivery subsystem is out of scope for
          the CRUD layer; the test asserts that both ``ri`` users
          are persisted and queryable, which is the orchestrator's
          recipient-lookup contract.

Edge cases verified alongside the happy path:

    * **Per-project ``bug_number`` scoping** — the unique
      constraint is ``UNIQUE(project_id, bug_number)``, not a
      global one. A second project starts its own numbering at
      ``BUG-1``. This pins DESIGN.md §1.9 "``bug_number`` is
      unique *per project*" and the service-layer
      :func:`_next_bug_number` scope.
    * **Auto-increment continues** — a second register against NEX
      Horizont yields ``BUG-13`` without the client sending
      ``bug_number``. Pins the ``MAX(bug_number) + 1`` formula
      across consecutive POSTs.
    * **Client-supplied ``bug_number`` is ignored** — the
      :class:`BugCreate` schema does not accept ``bug_number``.
      A payload that tries to set it is rejected (Pydantic
      ``extra='ignore'`` semantics mean the extra field is
      silently dropped; the service still auto-assigns). The test
      pins that the server-assigned number wins over anything the
      client might try to send — a small hedge against a future
      schema change that accidentally opens the field.
    * **Empty ``title``** — BEHAVIOR.md §3.15 step 2 names
      "Názov" as a mandatory form field. ``BugCreate.title`` has
      ``min_length=1``, so an empty string → HTTP 422. Nothing is
      written; the pre-existing seeded count is preserved.
    * **Invalid ``severity``** — the CHECK constraint pins
      ``severity IN ('critical', 'major', 'minor')``; the
      Pydantic ``BugSeverity`` literal mirrors it. A payload with
      ``severity='trivial'`` → HTTP 422 at the schema layer.

Auth note:
    Same as the rest of the Feat 7 integration tests — the router
    layer does not wire a JWT dependency yet, so the "Actor is
    member of project" precondition is satisfied by persisting
    the actor with a ``project_members`` row and passing
    ``created_by`` on the payload. Role enforcement is a separate
    auth-middleware concern.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.db.models.bugs import Bug
from backend.db.models.foundation import User
from backend.db.models.projects import Project, ProjectMember

# ---------------------------------------------------------------------------
# Precondition fixtures — Nazar (shu_junior) as a member of NEX Horizont,
# plus Zoltán and Tibor (ri) as the notification recipients.
# ---------------------------------------------------------------------------


@pytest.fixture()
def zoltan(db_session) -> User:
    """Persist Zoltán — the ``ri_director`` notification recipient.

    BEHAVIOR.md §3.15 postcondition line 643: "Notifikácia Zoltánovi
    a Tiborovi (ri role)". The notification subsystem is out of
    scope at the CRUD layer; the fixture seeds the row so the
    orchestrator's recipient-lookup query (``role='ri'``) returns
    a non-empty set, which is the observable precondition.
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
    """Persist Tibor — the ``ri_senior`` notification recipient.

    BEHAVIOR.md §3.15 postcondition line 643 names both ``ri``
    users explicitly. Seeded alongside Zoltán so the "all ``ri``
    users get notified" recipient-lookup returns exactly the two
    directors, not one.
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
    """Persist Nazar — the ``shu_junior`` actor from BEHAVIOR.md §1.1.

    BEHAVIOR.md §3.15 calls Nazar out by name in the workflow
    title ("Nazar (alebo ktokoľvek) zaregistruje bug"). He is the
    lowest-role member of the ICC team; register_bug is
    intentionally open to every role — the ``Actor`` line at
    §3.15 lists ``shu_junior`` first, then ``ha_medior``,
    ``ri_director`` and ``ri_senior``. The worked example is
    Nazar filing a bug found during manual testing.
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
    """Persist Dominik — the ``ha_medior`` actor.

    §3.15 also names ``ha_medior`` as a valid actor. Seeded so the
    companion "any member can register" test can submit as him
    without colliding on the shared fixture graph.
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
    """Persist the NEX Horizont project with every actor as a member.

    §3.15 precondition line 628: "Actor je prihlásený a je členom
    projektu". All four actors are added so individual tests can
    switch between them without re-provisioning the fixture graph.
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
def preexisting_eleven_bugs(db_session, nex_horizont, zoltan) -> list[Bug]:
    """Seed eleven prior bugs against NEX Horizont so BUG-012 is the next.

    §3.15 step 4 names "BUG-012" explicitly — the twelfth bug in
    the project. The service auto-assigns ``bug_number`` as
    ``MAX(bug_number) + 1`` per project (:mod:`backend.services.bug`
    :func:`_next_bug_number`), so to reproduce the worked example
    faithfully the fixture seeds ``bug_number=1..11`` directly. The
    eleven seeded bugs are all ``status='resolved'`` to keep the
    "open bugs" query uncontaminated; the register_bug workflow
    itself does not consult the status of prior bugs.
    """
    seeded: list[Bug] = []
    for i in range(1, 12):
        bug = Bug(
            project_id=nex_horizont.id,
            bug_number=i,
            title=f"Prior bug #{i}",
            description=f"Historical bug {i} against NEX Horizont.",
            severity="minor",
            status="resolved",
            source="internal",
            created_by=zoltan.id,
        )
        db_session.add(bug)
        seeded.append(bug)
    db_session.flush()
    return seeded


# ---------------------------------------------------------------------------
# Helpers — build payloads that mirror the §3.15 worked example.
# ---------------------------------------------------------------------------


# BEHAVIOR.md §3.15 step 2 — worked-example form values.
BUG_TITLE = "PAB detail page crashes on empty phone field"
BUG_DESCRIPTION = (
    "Reprodukcia:\n"
    "1. Otvor PAB (Katalóg partnerov) → detail partnera.\n"
    "2. Vymaž hodnotu poľa 'Telefón' a klikni 'Uložiť'.\n"
    "3. Stránka havaruje s ``TypeError: Cannot read properties of null``.\n\n"
    "Očakávané: tichá validácia, ponechať starú hodnotu alebo prijať NULL.\n"
    "Skutočné: crash + biela obrazovka."
)
BUG_SEVERITY = "major"
BUG_SOURCE = "internal"
BUG_ENVIRONMENT = "development"


def _bug_payload(project_id: uuid.UUID, reporter_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    """Build a JSON payload for ``POST /api/v1/bugs``.

    Defaults mirror the §3.15 worked example exactly. Overrides let
    individual tests swap fields (e.g. empty ``title`` for the 422
    edge case, or a different ``severity``).
    """
    payload: dict[str, Any] = {
        "project_id": str(project_id),
        "title": BUG_TITLE,
        "description": BUG_DESCRIPTION,
        "severity": BUG_SEVERITY,
        "source": BUG_SOURCE,
        "environment": BUG_ENVIRONMENT,
        "created_by": str(reporter_id),
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.15 end-to-end, BUG-012.
# ---------------------------------------------------------------------------


class TestRegisterBugHappyPath:
    """End-to-end walkthrough of workflow §3.15 against the real app."""

    def test_full_workflow_registers_bug_012_against_nex_horizont(
        self,
        client,
        db_session,
        nazar,
        zoltan,
        tibor,
        nex_horizont,
        preexisting_eleven_bugs,
    ):
        """Drive steps 1-4 of the workflow and verify every postcondition.

        The worked example from BEHAVIOR.md §3.15 is reproduced
        faithfully: Nazar (``shu_junior``) registers the PAB
        detail-page crash as BUG-012 in NEX Horizont, with
        severity ``major``, source ``internal`` and environment
        ``development``. The eleven pre-existing bugs make
        ``bug_number`` land at ``12`` naturally.
        """
        # --- Step 1: Nazar opens the "Bugy" tab. The bugs list
        # returns the eleven pre-existing rows. The UI's default
        # ordering is ``created_at DESC`` (service layer), so the
        # most recent seeded bug appears first.
        initial_list = client.get(
            "/api/v1/bugs",
            params={"project_id": str(nex_horizont.id)},
        )
        assert initial_list.status_code == 200, initial_list.text
        assert initial_list.json()["total"] == 11
        # Pre-seed ``bug_number`` sequence is 1..11 — assertion
        # uses a set because order is ``created_at DESC`` (seeded
        # in the same flush, order is therefore undefined but the
        # membership is not).
        seeded_numbers = {row["bug_number"] for row in initial_list.json()["items"]}
        assert seeded_numbers == set(range(1, 12))

        # --- Step 2: Nazar fills in the form. Client-side only —
        # no HTTP round-trip. The worked-example values live in
        # module-level constants above.

        # --- Step 3: Nazar clicks "Zaregistrovať bug". The
        # service auto-assigns ``bug_number`` as
        # ``MAX(bug_number) + 1`` — 12 for this project — and
        # defaults ``status`` to ``'new'`` via ``server_default``.
        create_resp = client.post(
            "/api/v1/bugs",
            json=_bug_payload(nex_horizont.id, nazar.id),
        )
        assert create_resp.status_code == 201, create_resp.text
        bug = create_resp.json()
        # §3.15 step 3 system response: "bug_number (auto-increment)".
        assert bug["bug_number"] == 12
        # §3.15 step 3 system response: "status='new'".
        assert bug["status"] == "new"
        # Payload fields round-trip verbatim.
        assert bug["project_id"] == str(nex_horizont.id)
        assert bug["title"] == BUG_TITLE
        assert bug["description"] == BUG_DESCRIPTION
        assert bug["severity"] == BUG_SEVERITY
        assert bug["source"] == BUG_SOURCE
        assert bug["environment"] == BUG_ENVIRONMENT
        assert bug["created_by"] == str(nazar.id)
        # Resolution-tracking fields are unset on register — they
        # are populated by the §3.18 resolve_bug workflow.
        assert bug["resolved_at"] is None
        assert bug["commit_hash"] is None
        assert bug["reported_by"] is None
        # Server-generated identity columns are populated.
        assert bug["id"]
        assert bug["created_at"]
        assert bug["updated_at"]

        bug_id = bug["id"]

        # --- Step 4 (system response): the confirmation string
        # "Bug BUG-012 bol zaregistrovaný. Čaká na akceptáciu." is
        # rendered from ``bug.bug_number`` and ``bug.status`` —
        # both asserted above. The UI label itself is not
        # observable at the CRUD layer.

        # --- Postcondition verification (HTTP) -------------------------
        # 1. The bug can be fetched by id (the "view bug detail"
        #    endpoint the UI hits when the user drills in).
        show_resp = client.get(f"/api/v1/bugs/{bug_id}")
        assert show_resp.status_code == 200
        assert show_resp.json()["id"] == bug_id
        assert show_resp.json()["bug_number"] == 12
        assert show_resp.json()["status"] == "new"

        # 2. §3.15 postcondition line 642: "Bug je viditeľný pre
        #    všetkých členov projektu". The bugs-page list query
        #    returns the new row alongside the seeded eleven — the
        #    rendered count is now 12 and BUG-012 appears.
        after_list = client.get(
            "/api/v1/bugs",
            params={"project_id": str(nex_horizont.id)},
        )
        assert after_list.status_code == 200
        assert after_list.json()["total"] == 12
        numbers_after = {row["bug_number"] for row in after_list.json()["items"]}
        assert numbers_after == set(range(1, 13))

        # 3. The ``status='new'`` filter (the "Čaká na akceptáciu"
        #    inbox for the §3.16 accept_bug_for_fix workflow)
        #    returns exactly BUG-012 — every seeded bug is
        #    ``resolved``.
        new_only = client.get(
            "/api/v1/bugs",
            params={"project_id": str(nex_horizont.id), "status": "new"},
        )
        assert new_only.status_code == 200
        assert new_only.json()["total"] == 1
        assert new_only.json()["items"][0]["id"] == bug_id
        assert new_only.json()["items"][0]["bug_number"] == 12

        # 4. The ``created_by=nazar.id`` filter lets the bugs page
        #    narrow to "my reports" — BUG-012 is the only one
        #    Nazar filed (the seeded eleven were filed by Zoltán).
        by_nazar = client.get(
            "/api/v1/bugs",
            params={
                "project_id": str(nex_horizont.id),
                "created_by": str(nazar.id),
            },
        )
        assert by_nazar.status_code == 200
        assert by_nazar.json()["total"] == 1
        assert by_nazar.json()["items"][0]["bug_number"] == 12

        # 5. §3.15 postcondition line 643: notification to Zoltán
        #    and Tibor (``role='ri'``). The delivery subsystem is
        #    out of scope; the orchestrator's recipient-lookup
        #    query — "all project members with role=ri" — is
        #    modelled here via the users endpoint. Both directors
        #    are persisted and active.
        ri_users = client.get("/api/v1/users", params={"role": "ri"})
        assert ri_users.status_code == 200
        ri_usernames = {row["username"] for row in ri_users.json()["items"]}
        assert {"zoltan", "tibor"} <= ri_usernames

        # --- Postcondition verification (DB state) ---------------------
        db_session.expire_all()

        # §3.15 postcondition line 641: ``status='new'``, unique
        # ``bug_number`` per project.
        persisted = db_session.get(Bug, uuid.UUID(bug_id))
        assert persisted is not None
        assert persisted.project_id == nex_horizont.id
        assert persisted.bug_number == 12
        assert persisted.title == BUG_TITLE
        assert persisted.description == BUG_DESCRIPTION
        assert persisted.severity == BUG_SEVERITY
        assert persisted.status == "new"
        assert persisted.source == BUG_SOURCE
        assert persisted.environment == BUG_ENVIRONMENT
        assert persisted.created_by == nazar.id
        assert persisted.resolved_at is None
        assert persisted.commit_hash is None
        assert persisted.reported_by is None
        # ``created_at`` and ``updated_at`` are populated by the
        # TimestampMixin server defaults; on INSERT they are equal.
        assert persisted.created_at is not None
        assert persisted.updated_at is not None

        # The UNIQUE(project_id, bug_number) constraint guarantees
        # the 12 rows own distinct numbers — assert the full set
        # materialises in the DB.
        all_bugs_for_project = (
            db_session.query(Bug).filter(Bug.project_id == nex_horizont.id).order_by(Bug.bug_number).all()
        )
        assert [row.bug_number for row in all_bugs_for_project] == list(range(1, 13))

    def test_any_project_member_may_register(
        self,
        client,
        db_session,
        dominik,
        nex_horizont,
    ):
        """BEHAVIOR.md §3.15 actor line — "Nazar (alebo ktokoľvek)".

        §3.15 lists four roles as valid actors: ``shu_junior``,
        ``ha_medior``, ``ri_director`` and ``ri_senior``. Nazar
        (``shu``) is covered by the worked example; Dominik
        (``ha``) is pinned here so the "any member can register"
        contract is not accidentally narrowed to ``role='shu'``
        or ``role='ri'``. The project starts empty of bugs for
        this test — the first register therefore produces
        ``BUG-1``, not ``BUG-12``, which also pins the
        ``_next_bug_number`` base case.
        """
        resp = client.post(
            "/api/v1/bugs",
            json=_bug_payload(
                nex_horizont.id,
                dominik.id,
                title="Migration script leaves orphaned rows in ``contacts``.",
                severity="minor",
            ),
        )
        assert resp.status_code == 201, resp.text
        bug = resp.json()
        assert bug["bug_number"] == 1
        assert bug["status"] == "new"
        assert bug["created_by"] == str(dominik.id)

        db_session.expire_all()
        persisted = db_session.get(Bug, uuid.UUID(bug["id"]))
        assert persisted is not None
        assert persisted.created_by == dominik.id
        assert persisted.bug_number == 1


# ---------------------------------------------------------------------------
# Edge cases — numbering scope, sequential auto-increment, form validation.
# ---------------------------------------------------------------------------


class TestRegisterBugEdgeCases:
    """Uniqueness, auto-increment and form-validation contracts.

    BEHAVIOR.md §3.15 pins three invariants on the bug-number
    axis — "auto-increment" (step 3), "unikátny ``bug_number`` v
    projekte" (postcondition line 641) and the implicit
    per-project scoping (UI references ``BUG-012`` without a
    project suffix because the number alone is unambiguous within
    a project). The edge cases below exercise each one.

    The two schema-validation edges — empty ``title`` and invalid
    ``severity`` — are the only client-side rejections the form
    can produce: every other field is optional or defaults via
    ``server_default``.
    """

    def test_bug_number_auto_increments_across_consecutive_registers(
        self,
        client,
        db_session,
        nazar,
        nex_horizont,
        preexisting_eleven_bugs,
    ):
        """Consecutive registers → BUG-12, BUG-13, BUG-14 in order.

        Pins the service-layer ``MAX(bug_number) + 1`` formula
        across multiple sequential POSTs against the same project.
        The fixture graph already puts ``MAX`` at 11; three
        registers in a row must produce 12, 13, 14 with no gaps.
        """
        numbers: list[int] = []
        for idx in range(3):
            resp = client.post(
                "/api/v1/bugs",
                json=_bug_payload(
                    nex_horizont.id,
                    nazar.id,
                    title=f"Sequential register #{idx}",
                ),
            )
            assert resp.status_code == 201, resp.text
            numbers.append(resp.json()["bug_number"])

        assert numbers == [12, 13, 14]

        # DB confirms the three new rows alongside the eleven
        # seeded ones.
        db_session.expire_all()
        all_numbers = sorted(
            row.bug_number for row in db_session.query(Bug).filter(Bug.project_id == nex_horizont.id).all()
        )
        assert all_numbers == list(range(1, 15))

    def test_bug_number_is_scoped_per_project(
        self,
        client,
        db_session,
        nazar,
        zoltan,
        nex_horizont,
        preexisting_eleven_bugs,
    ):
        """A second project starts its own numbering at BUG-1.

        DESIGN.md §1.9 / the DB ``UNIQUE(project_id, bug_number)``
        constraint (``uq_bugs_project_id_bug_number``) is scoped
        *per project*. NEX Horizont owns 1..11 (seeded) and is
        about to own 12; a fresh sibling project (``NEX Marina``)
        therefore starts at 1 on its first register, not at 13.
        """
        # Seed a second project — singlemodule is fine, the
        # per-project scoping does not care about category.
        nex_marina = Project(
            name="NEX Marina",
            slug="nex-marina",
            category="singlemodule",
            description="Marina booking — singlemodule sibling.",
            created_by=zoltan.id,
        )
        db_session.add(nex_marina)
        db_session.flush()
        db_session.add(ProjectMember(project_id=nex_marina.id, user_id=nazar.id))
        db_session.flush()

        # Register against NEX Horizont — lands at 12.
        horizont_resp = client.post(
            "/api/v1/bugs",
            json=_bug_payload(nex_horizont.id, nazar.id),
        )
        assert horizont_resp.status_code == 201, horizont_resp.text
        assert horizont_resp.json()["bug_number"] == 12

        # Register against NEX Marina — lands at 1, not 13.
        marina_resp = client.post(
            "/api/v1/bugs",
            json=_bug_payload(
                nex_marina.id,
                nazar.id,
                title="Marina booking: double-booking on same slot.",
                severity="critical",
            ),
        )
        assert marina_resp.status_code == 201, marina_resp.text
        assert marina_resp.json()["bug_number"] == 1
        assert marina_resp.json()["project_id"] == str(nex_marina.id)

        # Both rows coexist; the ``(project_id, bug_number)`` pair
        # is unique on each side.
        db_session.expire_all()
        horizont_bug = db_session.get(Bug, uuid.UUID(horizont_resp.json()["id"]))
        marina_bug = db_session.get(Bug, uuid.UUID(marina_resp.json()["id"]))
        assert horizont_bug is not None
        assert marina_bug is not None
        assert horizont_bug.bug_number == 12
        assert marina_bug.bug_number == 1
        assert horizont_bug.project_id != marina_bug.project_id

    def test_client_supplied_bug_number_is_ignored_server_assigns(
        self,
        client,
        db_session,
        nazar,
        nex_horizont,
        preexisting_eleven_bugs,
    ):
        """Client-sent ``bug_number`` must not override the service.

        :class:`BugCreate` does not declare ``bug_number`` as a
        field; Pydantic's default extra-field policy silently
        drops it. The test pins that contract — a malicious or
        confused client cannot skip the queue by sending
        ``bug_number=999``; the server still assigns the next
        natural value (``12``).
        """
        resp = client.post(
            "/api/v1/bugs",
            json={
                **_bug_payload(nex_horizont.id, nazar.id),
                "bug_number": 999,
            },
        )
        assert resp.status_code == 201, resp.text
        # Server-side assignment wins.
        assert resp.json()["bug_number"] == 12

        db_session.expire_all()
        persisted = db_session.get(Bug, uuid.UUID(resp.json()["id"]))
        assert persisted is not None
        assert persisted.bug_number == 12

    def test_empty_title_is_rejected_with_422(
        self,
        client,
        db_session,
        nazar,
        nex_horizont,
        preexisting_eleven_bugs,
    ):
        """Empty ``title`` → HTTP 422, nothing written.

        §3.15 step 2 names "Názov" as a mandatory form field; the
        UI cannot submit the form without it, and the API
        schema's ``min_length=1`` is the server-side mirror of
        that constraint. A payload with ``title=""`` must be
        rejected at the Pydantic layer — HTTP 422 with no row
        written.
        """
        resp = client.post(
            "/api/v1/bugs",
            json=_bug_payload(nex_horizont.id, nazar.id, title=""),
        )
        assert resp.status_code == 422, resp.text

        # The count is still 11 — no new row was written.
        db_session.expire_all()
        total = db_session.query(Bug).filter(Bug.project_id == nex_horizont.id).count()
        assert total == 11

    def test_invalid_severity_is_rejected_with_422(
        self,
        client,
        db_session,
        nazar,
        nex_horizont,
        preexisting_eleven_bugs,
    ):
        """Severity outside {critical, major, minor} → HTTP 422.

        The DB CHECK constraint ``ck_bugs_severity`` pins the
        allowed set; the :data:`BugSeverity` Pydantic literal
        mirrors it. A payload with ``severity='trivial'`` is
        rejected at the schema layer before any DB touch — HTTP
        422 with no row written. This pins the form's
        ``Závažnosť`` dropdown contract from §3.15 step 2.
        """
        resp = client.post(
            "/api/v1/bugs",
            json=_bug_payload(nex_horizont.id, nazar.id, severity="trivial"),
        )
        assert resp.status_code == 422, resp.text

        db_session.expire_all()
        total = db_session.query(Bug).filter(Bug.project_id == nex_horizont.id).count()
        assert total == 11

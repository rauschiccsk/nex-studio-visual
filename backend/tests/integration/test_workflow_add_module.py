"""Integration test for BEHAVIOR.md §3.7 ``workflow:add_module``.

Exercises the full happy path of the **add_module** workflow end-to-end
through the real FastAPI ``app``. The workflow is Zoltán
(``ri_director`` per BEHAVIOR.md §1.1) — or any ``ri``-role user —
adding a new module to an existing ``multimodule`` project from the
"Moduly" tab. The worked example (§3.7 "Konkrétny príklad") adds the
``STK`` module (``Skladové karty zásob``, category ``Sklad``) to NEX
Horizont with a single dependency on the pre-existing ``GSC``
(``Globálne skladové karty``) module.

    Precondition (per BEHAVIOR.md §3.7):
        * A project exists with ``category='multimodule'``.
        * The actor has role ``ri`` (``ri_director`` Zoltán or
          ``ri_senior`` Tibor per BEHAVIOR.md §1.1) and is a member of
          the project.
        * The code of the new module (``STK`` in the worked example)
          does not yet exist in the project (``UNIQUE(project_id,
          code)``).
        * Every declared dependency already exists — ``GSC`` is
          pre-seeded as a ``project_modules`` row the new edge can
          target.

    Steps (per BEHAVIOR.md §3.7):
        1. Zoltán opens NEX Horizont → tab "Moduly" — the UI fetches
           the module registry. Modelled here as
           ``GET /api/v1/project-modules?project_id=...``; before step
           2 the list returns only the pre-seeded ``GSC`` row.
        2. Zoltán clicks "Pridať modul" — the form opens. Client-side
           only; no HTTP round-trip.
        3. Zoltán fills in ``Kód="STK"``, ``Názov="Skladové karty
           zásob"``, ``Kategória="Sklad"``, ``Závislosti=[GSC]`` —
           client-side form state only.
        4. Zoltán clicks "Pridať" — the orchestrator drives two
           sequential POSTs:
                a. ``POST /api/v1/project-modules`` with the new
                   module's ``(project_id, code, name, category)``.
                   The service validates uniqueness of
                   ``(project_id, code)`` and persists the row with
                   ``status='planned'`` (DB ``server_default``).
                b. ``POST /api/v1/module-dependencies`` once per
                   declared dependency — one POST in the §3.7 worked
                   example (``STK → GSC``). The service validates
                   the ``(module_id, depends_on_module_id)`` pair is
                   not a self-loop and is unique.
        5. — (system) — the new ``project_modules`` row and each
           ``module_dependencies`` edge are persisted with
           server-generated ``id`` / ``created_at`` / ``updated_at``.

    Postcondition (per BEHAVIOR.md §3.7):
        * ``project_modules`` row exists with ``code='STK'`` and
          ``status='planned'``.
        * ``module_dependencies`` contains the edge ``STK → GSC``.
        * The UI shows ``blocked_by=['GSC']`` as long as ``GSC`` is
          not ``done`` — modelled here as the list query the registry
          runs per row: "what does this module depend on" (every
          ``module_dependencies`` edge whose ``module_id`` is the
          module in question) filtered by "which of those prerequisite
          modules is not yet ``done``". The answer is exactly the
          ``blocked_by`` UI label.

Edge cases verified alongside the happy path:

    * **Duplicate code within the same project** — Zoltán resubmits
      the form with ``Kód='STK'`` (or any other already-taken code)
      in NEX Horizont. The service rejects it with a clean
      :class:`ValueError` that the router translates to HTTP 409
      (BEHAVIOR.md §3.7 precondition 3 — "Kód modulu ešte neexistuje
      v projekte"). No second row is persisted.
    * **Same code in a different project is allowed** — the
      ``UNIQUE(project_id, code)`` constraint is scoped per project.
      A second project may therefore have its own ``STK`` row without
      conflict (DESIGN.md §1.5 ProjectModule — "``code`` is unique
      *per project*"). This pins the scope of the duplicate-code
      edge case above.
    * **Self-loop dependency** — a dependency whose ``module_id``
      equals its ``depends_on_module_id`` is a one-hop cycle the
      service rejects with HTTP 409. A module cannot depend on
      itself (DESIGN.md §1.2 ``module_dependencies`` — "self-loops
      are rejected pre-emptively").
    * **Duplicate dependency edge** — re-adding the same ``STK → GSC``
      edge is rejected with HTTP 409. The
      ``UNIQUE(module_id, depends_on_module_id)`` natural key is
      validated pre-flush so the second POST does not silently
      succeed or surface a raw ``IntegrityError``.

Auth note:
    The current codebase (Feats 0–6) wires routers directly without a
    JWT dependency, so the integration test does not exercise a login
    flow. The "role=ri, member of project" precondition is satisfied
    by persisting the actor with ``role='ri'`` and adding them to
    ``project_members``. Role enforcement at the router level is a
    separate concern covered by future auth-middleware tests.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import (
    ModuleDependency,
    Project,
    ProjectMember,
    ProjectModule,
)

# ---------------------------------------------------------------------------
# Precondition fixtures — Zoltán (ri_director) / Tibor (ri_senior), the
# NEX Horizont project with Zoltán as a member, and the pre-existing
# ``GSC`` module that the new ``STK`` will depend on.
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
    """Persist Tibor — the ``ri_senior`` actor from BEHAVIOR.md §1.1.

    BEHAVIOR.md §3.7 lists the actor as "[[actor:ri_director]] alebo
    [[actor:ri_senior]]" — both resolve to ``role='ri'`` at the DB
    level. Zoltán is the worked-example actor; Tibor is pinned as
    an equally valid caller by the companion happy-path test.
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
    """Persist the NEX Horizont project and add both ``ri`` users as members.

    The §3.7 precondition requires the actor to be a member of a
    ``multimodule`` project. Both Zoltán and Tibor are added so the
    happy-path and ``ri_senior`` equivalence tests reuse the same
    project row.
    """
    project = Project(
        name="NEX Horizont",
        slug="nex-horizont",
        category="multimodule",  # §3.7 precondition line 1.
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
def gsc_module(db_session, nex_horizont) -> ProjectModule:
    """Persist the pre-existing ``GSC`` module — the §3.7 dependency target.

    BEHAVIOR.md §3.7 step 4 system response: "unikátny kód 'STK' v
    projekte, existencia závislostí (GSC modul existuje)". The new
    ``STK`` module will declare a dependency on this row, so it must
    exist before the workflow runs. Seeded in ``in_design`` — not
    ``done`` — so the "blocked_by=['GSC']" UI contract in the §3.7
    postcondition is observable.
    """
    module = ProjectModule(
        project_id=nex_horizont.id,
        code="GSC",
        name="Globálne skladové karty",
        category="Sklad",
        status="in_design",  # Not ``done`` → blocks STK.
    )
    db_session.add(module)
    db_session.flush()
    return module


# ---------------------------------------------------------------------------
# Helpers — build payloads that mirror the §3.7 worked example.
# ---------------------------------------------------------------------------


# BEHAVIOR.md §3.7 step 3: Kód="STK", Názov="Skladové karty zásob",
# Kategória="Sklad", Závislosti=[GSC].
STK_CODE = "STK"
STK_NAME = "Skladové karty zásob"
STK_CATEGORY = "Sklad"


def _module_payload(project_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    """Build a JSON payload for ``POST /api/v1/project-modules``.

    Defaults mirror the §3.7 worked example exactly (``STK`` /
    ``Skladové karty zásob`` / ``Sklad``). Overrides let individual
    tests swap fields (e.g. duplicate ``code`` for the collision edge
    case, or a different project for the cross-project scoping test).
    """
    payload: dict[str, Any] = {
        "project_id": str(project_id),
        "code": STK_CODE,
        "name": STK_NAME,
        "category": STK_CATEGORY,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.7 end-to-end.
# ---------------------------------------------------------------------------


class TestAddModuleHappyPath:
    """End-to-end walkthrough of workflow §3.7 against the real app."""

    def test_full_workflow_adds_stk_with_dependency_on_gsc(
        self,
        client,
        db_session,
        zoltan,
        nex_horizont,
        gsc_module,
    ):
        """Drive steps 1-5 of the workflow and verify every postcondition.

        The test asserts both the HTTP contract (status codes, payload
        shape) and the database state after each step. The worked
        example from BEHAVIOR.md §3.7 is reproduced faithfully: Zoltán
        adds ``STK`` (``Skladové karty zásob``, ``Sklad``) to NEX
        Horizont with a single dependency on the pre-existing
        ``GSC`` module.
        """
        # --- Step 1: Zoltán opens the "Moduly" tab. The module
        # registry query returns the pre-existing modules so the UI
        # can render them; in this fixture ``GSC`` is the only row.
        initial_list = client.get(
            "/api/v1/project-modules",
            params={"project_id": str(nex_horizont.id)},
        )
        assert initial_list.status_code == 200, initial_list.text
        assert initial_list.json()["total"] == 1
        assert [row["code"] for row in initial_list.json()["items"]] == ["GSC"]

        # --- Step 2: Zoltán clicks "Pridať modul". Client-side only
        # — the form opens with empty fields. No HTTP round-trip.
        # --- Step 3: Zoltán fills in the form. Client-side only.

        # --- Step 4a: Zoltán clicks "Pridať" — the orchestrator POSTs
        # the new module. The service validates
        # ``UNIQUE(project_id, code)`` pre-flush; a duplicate surfaces
        # as HTTP 409 (exercised in the edge-cases section below). The
        # DB fills in ``id``, ``created_at``, ``updated_at`` and
        # ``status`` defaults to ``planned`` (``server_default``) —
        # §3.7 step 5 / postcondition line 1.
        create_module_resp = client.post(
            "/api/v1/project-modules",
            json=_module_payload(nex_horizont.id),
        )
        assert create_module_resp.status_code == 201, create_module_resp.text
        stk = create_module_resp.json()
        assert stk["project_id"] == str(nex_horizont.id)
        assert stk["code"] == STK_CODE
        assert stk["name"] == STK_NAME
        assert stk["category"] == STK_CATEGORY
        # §3.7 step 5 / postcondition line 1: "status='planned'".
        assert stk["status"] == "planned"
        assert stk["design_doc_path"] is None
        assert stk["id"]
        assert stk["created_at"]
        assert stk["updated_at"]

        stk_id = stk["id"]

        # --- Step 4b: the orchestrator POSTs one dependency edge per
        # declared prerequisite. The §3.7 worked example has exactly
        # one (``STK → GSC``). The service validates the pair is not
        # a self-loop and is unique; either failure surfaces as HTTP
        # 409.
        create_dep_resp = client.post(
            "/api/v1/module-dependencies",
            json={
                "module_id": stk_id,
                "depends_on_module_id": str(gsc_module.id),
            },
        )
        assert create_dep_resp.status_code == 201, create_dep_resp.text
        edge = create_dep_resp.json()
        assert edge["module_id"] == stk_id
        assert edge["depends_on_module_id"] == str(gsc_module.id)
        assert edge["id"]
        assert edge["created_at"]
        assert edge["updated_at"]

        # --- Postcondition verification (HTTP) -------------------------
        # 1. The module registry lists STK alongside GSC.
        after_list = client.get(
            "/api/v1/project-modules",
            params={"project_id": str(nex_horizont.id)},
        )
        assert after_list.status_code == 200
        assert after_list.json()["total"] == 2
        codes = {row["code"] for row in after_list.json()["items"]}
        assert codes == {"GSC", "STK"}

        # 2. ``STK`` can be looked up directly — the UI hits this
        #    endpoint when the user drills into the row.
        show_stk = client.get(f"/api/v1/project-modules/{stk_id}")
        assert show_stk.status_code == 200
        assert show_stk.json()["code"] == STK_CODE
        assert show_stk.json()["status"] == "planned"

        # 3. The dependency edge ``STK → GSC`` is visible via the
        #    "what does this module depend on" query the
        #    ``ModuleGraph`` / registry UI runs per row.
        deps_of_stk = client.get(
            "/api/v1/module-dependencies",
            params={"module_id": stk_id},
        )
        assert deps_of_stk.status_code == 200
        assert deps_of_stk.json()["total"] == 1
        assert [row["depends_on_module_id"] for row in deps_of_stk.json()["items"]] == [str(gsc_module.id)]

        # 4. The inverse query ("which modules depend on GSC") also
        #    surfaces the edge — it backs the dependency-graph
        #    visualisation in ``ModuleGraph`` (DESIGN.md §3.2).
        dependents_of_gsc = client.get(
            "/api/v1/module-dependencies",
            params={"depends_on_module_id": str(gsc_module.id)},
        )
        assert dependents_of_gsc.status_code == 200
        assert dependents_of_gsc.json()["total"] == 1
        assert [row["module_id"] for row in dependents_of_gsc.json()["items"]] == [stk_id]

        # 5. ``blocked_by=['GSC']`` contract — §3.7 postcondition
        #    line 3. The UI derives the label by intersecting
        #    "prerequisites of this module" with "prerequisites not
        #    yet ``done``". Model that derivation here with two
        #    public endpoints.
        blocker_module_ids: list[str] = []
        for dep in deps_of_stk.json()["items"]:
            prereq = client.get(f"/api/v1/project-modules/{dep['depends_on_module_id']}")
            assert prereq.status_code == 200
            if prereq.json()["status"] != "done":
                blocker_module_ids.append(prereq.json()["code"])
        assert blocker_module_ids == ["GSC"]

        # --- Postcondition verification (DB state) ---------------------
        db_session.expire_all()

        # 1. ``project_modules`` row exists with the §3.7 fields.
        persisted_module = db_session.get(ProjectModule, uuid.UUID(stk_id))
        assert persisted_module is not None
        assert persisted_module.project_id == nex_horizont.id
        assert persisted_module.code == STK_CODE
        assert persisted_module.name == STK_NAME
        assert persisted_module.category == STK_CATEGORY
        # §3.7 postcondition line 1: "status='planned'".
        assert persisted_module.status == "planned"
        # DESIGN.md path is populated later (workflow §3.5); at add
        # time it is NULL.
        assert persisted_module.design_doc_path is None

        # 2. ``module_dependencies`` contains the edge ``STK → GSC``.
        persisted_edge = db_session.get(ModuleDependency, uuid.UUID(edge["id"]))
        assert persisted_edge is not None
        assert persisted_edge.module_id == persisted_module.id
        assert persisted_edge.depends_on_module_id == gsc_module.id

        # 3. The inverse direction of the edge (GSC → STK) is NOT
        #    auto-created — dependencies are directed, per
        #    DESIGN.md §1.2. Pinning absence here guards against a
        #    future regression that accidentally inserts a reverse
        #    edge.
        inverse_edge_list = client.get(
            "/api/v1/module-dependencies",
            params={
                "module_id": str(gsc_module.id),
                "depends_on_module_id": stk_id,
            },
        )
        assert inverse_edge_list.status_code == 200
        assert inverse_edge_list.json()["total"] == 0

    def test_ri_senior_may_also_add_a_module(
        self,
        client,
        db_session,
        tibor,
        nex_horizont,
        gsc_module,
    ):
        """``ri_senior`` (Tibor) is an equally valid actor per §3.7.

        BEHAVIOR.md §3.7 lists the actor as "[[actor:ri_director]]
        alebo [[actor:ri_senior]]" — both roles resolve to
        ``role='ri'`` at the DB level. The router accepts any member
        of a ``multimodule`` project; enforcement is identical
        regardless of which specific ``ri`` user clicks "Pridať".
        Zoltán is the worked-example actor covered above; this test
        pins Tibor's equivalence by adding a different module code
        so the two tests do not collide on ``UNIQUE(project_id,
        code)``.
        """
        resp = client.post(
            "/api/v1/project-modules",
            json=_module_payload(
                nex_horizont.id,
                code="PAB",
                name="Katalóg partnerov",
                category="Katalógy",
            ),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["code"] == "PAB"
        assert resp.json()["status"] == "planned"

        db_session.expire_all()
        persisted = db_session.get(ProjectModule, uuid.UUID(resp.json()["id"]))
        assert persisted is not None
        assert persisted.code == "PAB"
        assert persisted.project_id == nex_horizont.id


# ---------------------------------------------------------------------------
# Edge cases — duplicate code, cross-project scoping, self-loop,
# duplicate edge.
# ---------------------------------------------------------------------------


class TestAddModuleEdgeCases:
    """Edge cases around the workflow's uniqueness and graph preconditions.

    BEHAVIOR.md §3.7 step 4 spells out the validation contract:
    "unikátny kód 'STK' v projekte, existencia závislostí". The
    uniqueness cheque translates directly into HTTP 409 at the
    router layer via the service-layer ``ValueError`` translation
    (``_map_value_error`` in
    :mod:`backend.api.routes.project_modules` and
    :mod:`backend.api.routes.module_dependencies`). The happy path
    already exercises "existencia závislostí" positively (``GSC``
    exists, the edge is created); the two graph-level invariants
    pinned below — no self-loop and no duplicate edge — are the
    natural-key contracts :class:`ModuleDependency` declares at
    DESIGN.md §1.2.
    """

    def test_duplicate_code_within_same_project_is_rejected_with_409(
        self,
        client,
        db_session,
        nex_horizont,
    ):
        """POST with a code that collides within the project → 409.

        BEHAVIOR.md §3.7 precondition line 3 "Kód modulu ešte
        neexistuje v projekte". The service validates uniqueness
        pre-flush and raises :class:`ValueError` (``... already
        exists in project ...``), which the router's
        ``_map_value_error`` translates to HTTP 409. No second row
        is created and the original module survives intact.

        The first module is created via the real API POST (not via
        the ``gsc_module`` fixture) so its savepoint commits cleanly
        before the conflict attempt. The conflict's router-level
        ``db.rollback()`` therefore only unwinds the failed second
        savepoint; the successfully-committed first row survives.
        """
        # Seed STK through the API so the savepoint commits and the
        # row survives the upcoming 409 rollback.
        first = client.post(
            "/api/v1/project-modules",
            json=_module_payload(nex_horizont.id),
        )
        assert first.status_code == 201, first.text
        stk_id = first.json()["id"]

        # Same ``(project_id, code)`` pair → rejected. Overriding
        # ``name`` makes the point that the collision is on ``code``
        # alone, not on any other field.
        conflict = client.post(
            "/api/v1/project-modules",
            json=_module_payload(
                nex_horizont.id,
                name="Skladové karty zásob — mirror",
            ),
        )
        assert conflict.status_code == 409, conflict.text
        assert "already exists" in conflict.json()["detail"].lower()

        # Only the original row survives. The list still reports one
        # module, still named per the original POST.
        db_session.expire_all()
        listing = client.get(
            "/api/v1/project-modules",
            params={"project_id": str(nex_horizont.id)},
        )
        assert listing.status_code == 200
        assert listing.json()["total"] == 1
        only = listing.json()["items"][0]
        assert only["id"] == stk_id
        assert only["code"] == STK_CODE
        assert only["name"] == STK_NAME

    def test_same_code_in_different_project_is_allowed(
        self,
        client,
        db_session,
        zoltan,
        nex_horizont,
        gsc_module,
    ):
        """``UNIQUE(project_id, code)`` is scoped per project.

        DESIGN.md §1.5 ProjectModule — "``code`` is unique *per
        project*. The same short code (e.g. ``'PAB'``) may therefore
        exist in several projects". This test pins the scope of the
        duplicate-code edge case above: ``STK`` in a second project
        (``NEX Marina``) succeeds even though NEX Horizont could
        hold its own ``STK``.
        """
        # Seed a second project — singlemodule is fine, the
        # per-project scoping does not care about category here.
        nex_marina = Project(
            name="NEX Marina",
            slug="nex-marina",
            category="singlemodule",
            description="Marina booking — singlemodule sibling.",
            created_by=zoltan.id,
        )
        db_session.add(nex_marina)
        db_session.flush()

        # Add ``STK`` to NEX Horizont first.
        horizont_resp = client.post(
            "/api/v1/project-modules",
            json=_module_payload(nex_horizont.id),
        )
        assert horizont_resp.status_code == 201, horizont_resp.text

        # Same code, different project — must succeed.
        marina_resp = client.post(
            "/api/v1/project-modules",
            json=_module_payload(nex_marina.id),
        )
        assert marina_resp.status_code == 201, marina_resp.text
        assert marina_resp.json()["project_id"] == str(nex_marina.id)
        assert marina_resp.json()["code"] == STK_CODE

        # Both rows coexist with the same code.
        db_session.expire_all()
        horizont_stk = db_session.get(ProjectModule, uuid.UUID(horizont_resp.json()["id"]))
        marina_stk = db_session.get(ProjectModule, uuid.UUID(marina_resp.json()["id"]))
        assert horizont_stk is not None
        assert marina_stk is not None
        assert horizont_stk.code == marina_stk.code == STK_CODE
        assert horizont_stk.project_id != marina_stk.project_id

    def test_self_loop_dependency_is_rejected_with_409(
        self,
        client,
        db_session,
        nex_horizont,
        gsc_module,
    ):
        """A dependency on self is a one-hop cycle — HTTP 409.

        DESIGN.md §1.2 ``module_dependencies``: "self-loops are
        rejected pre-emptively" — the service is the enforcement
        point because the DB has no CHECK constraint that can
        cheaply express the predicate. A caller that (e.g. through
        a stale UUID copy-paste) submits ``module_id ==
        depends_on_module_id`` must receive a clean HTTP 409, not
        a silent graph cycle.
        """
        # Create ``STK`` first so it has a valid UUID to self-loop
        # against.
        stk_resp = client.post(
            "/api/v1/project-modules",
            json=_module_payload(nex_horizont.id),
        )
        assert stk_resp.status_code == 201, stk_resp.text
        stk_id = stk_resp.json()["id"]

        # STK → STK is a self-loop.
        loop = client.post(
            "/api/v1/module-dependencies",
            json={
                "module_id": stk_id,
                "depends_on_module_id": stk_id,
            },
        )
        assert loop.status_code == 409, loop.text
        assert "self-loop" in loop.json()["detail"].lower()

        # No edge was persisted for STK.
        db_session.expire_all()
        deps = client.get(
            "/api/v1/module-dependencies",
            params={"module_id": stk_id},
        )
        assert deps.status_code == 200
        assert deps.json()["total"] == 0

    def test_duplicate_dependency_edge_is_rejected_with_409(
        self,
        client,
        db_session,
        nex_horizont,
        gsc_module,
    ):
        """Re-adding the same ``STK → GSC`` edge → HTTP 409.

        :class:`ModuleDependency` declares ``UNIQUE(module_id,
        depends_on_module_id)`` as its natural key (DESIGN.md §1.2).
        The service validates the pair pre-flush so a caller that
        double-clicks "Pridať" does not receive a raw
        :class:`~sqlalchemy.exc.IntegrityError` — the second POST
        is a clean HTTP 409 and exactly one row persists.
        """
        # Create STK.
        stk_resp = client.post(
            "/api/v1/project-modules",
            json=_module_payload(nex_horizont.id),
        )
        assert stk_resp.status_code == 201, stk_resp.text
        stk_id = stk_resp.json()["id"]

        # First edge — succeeds.
        first = client.post(
            "/api/v1/module-dependencies",
            json={
                "module_id": stk_id,
                "depends_on_module_id": str(gsc_module.id),
            },
        )
        assert first.status_code == 201, first.text
        edge_id = first.json()["id"]

        # Second identical edge — rejected.
        duplicate = client.post(
            "/api/v1/module-dependencies",
            json={
                "module_id": stk_id,
                "depends_on_module_id": str(gsc_module.id),
            },
        )
        assert duplicate.status_code == 409, duplicate.text
        assert "already exists" in duplicate.json()["detail"].lower()

        # Exactly one edge in the DB for (STK, GSC).
        db_session.expire_all()
        deps = client.get(
            "/api/v1/module-dependencies",
            params={
                "module_id": stk_id,
                "depends_on_module_id": str(gsc_module.id),
            },
        )
        assert deps.status_code == 200
        assert deps.json()["total"] == 1
        assert deps.json()["items"][0]["id"] == edge_id

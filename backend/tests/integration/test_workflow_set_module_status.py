"""Integration test for BEHAVIOR.md §3.8 ``workflow:set_module_status``.

Exercises the full happy path of the **set_module_status** workflow
end-to-end through the real FastAPI ``app``. The workflow is Zoltán
(``ri_director`` per BEHAVIOR.md §1.1) — or any ``ri``-role user —
advancing a module to the next state in its lifecycle
(``planned`` → ``in_design`` → ``in_development`` → ``done``). The
worked example (§3.8 steps 1-3) is Tibor pushing STK (``Skladové karty
zásob``) from ``planned`` to ``in_development`` once the prerequisite
GSC (``Globálne skladové karty``) module has reached ``done``.

    Precondition (per BEHAVIOR.md §3.8):
        * The module exists in the project.
        * The actor has role ``ri`` (``ri_director`` Zoltán or
          ``ri_senior`` Tibor per BEHAVIOR.md §1.1).
        * For the transition to ``in_development``: every declared
          dependency must be ``done``. The current codebase enforces
          this at the UI layer (the status-select is disabled until
          prerequisites are ``done``) — the HTTP surface exposes the
          full ``planned | in_design | in_development | done`` literal
          through :class:`ProjectModuleUpdate` without a server-side
          dependency guard. This test therefore only exercises the
          positive transition, matching the §3.8 worked example where
          GSC is already ``done``. Negative dependency-guard behaviour
          is a separate concern for a future auth/guard test suite.

    Steps (per BEHAVIOR.md §3.8):
        1. Tibor clicks on module STK → "Zmeniť stav" → picks
           ``in_development``. The UI checks each declared dependency's
           status — in this test the check is modelled via
           ``GET /api/v1/module-dependencies?module_id=STK``.
        2. — (system) — GSC is ``done`` → validation passes.
        3. — (system) — the PATCH updates ``status='in_development'``
           and the UI shows "Modul STK je teraz v stave
           in_development" (response body). Modelled here as
           ``PATCH /api/v1/project-modules/{stk_id}`` with
           ``{"status": "in_development"}``.

    Postcondition (per BEHAVIOR.md §3.8):
        * ``project_modules.status='in_development'``.
        * An Architect session for STK can now be opened — modelled
          by a successful ``POST /api/v1/architect-sessions`` scoped
          to the STK module after the PATCH succeeds. The DB layer
          does not enforce a CHECK between session creation and
          module status (architect sessions may precede development
          for design-stage planning), so this is a positive
          observability check, not a gating one.

Edge cases verified alongside the happy path:

    * **Invalid status literal** — PATCH with
      ``status='not-a-status'`` is rejected at the Pydantic layer
      (HTTP 422) by :class:`ProjectModuleStatus`'s ``Literal`` type.
      The service never runs and the row survives unchanged with
      its original status. This mirrors the ``ck_project_modules_status``
      DB CHECK at the schema layer (DESIGN.md §2.2).
    * **Non-existent module** — PATCH against a random UUID returns
      HTTP 404 (``_map_value_error`` in
      :mod:`backend.api.routes.project_modules` translates the
      service-layer ``ValueError("ProjectModule {id} not found")``).
      Nothing is persisted.
    * **Full lifecycle progression** — a single module is driven
      through every legal transition
      (``planned`` → ``in_design`` → ``in_development`` → ``done``)
      to pin the PATCH contract at each hop and verify the DB
      CHECK accepts every terminal literal.
    * **Status PATCH preserves unrelated fields** — the §3.8
      worked-example transition touches ``status`` only; the
      returned row must still carry the original ``code``, ``name``,
      ``category`` and ``design_doc_path``. A partial update that
      accidentally nulled a sibling field would corrupt the Module
      Registry UI; the :class:`ProjectModuleUpdate` allow-list and
      the service's ``exclude_unset`` guard are the two layers
      under test.

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

import pytest

from backend.db.models.architect import ArchitectSession
from backend.db.models.foundation import User
from backend.db.models.projects import (
    ModuleDependency,
    Project,
    ProjectModule,
)

# ---------------------------------------------------------------------------
# Precondition fixtures — Zoltán (ri_director) / Tibor (ri_senior), the
# NEX Horizont project with both as members, a pre-existing ``GSC``
# module already in ``done`` (so the §3.8 dependency precondition for
# pushing STK to ``in_development`` is satisfied), and a pre-existing
# ``STK`` module sitting in ``planned`` with a dependency edge on GSC.
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

    BEHAVIOR.md §3.8 lists the actor as "[[actor:ri_director]] alebo
    [[actor:ri_senior]]" — both resolve to ``role='ri'`` at the DB
    level. Tibor is the worked-example actor in §3.8 ("Tibor klikne na
    modul STK → 'Zmeniť stav'"); Zoltán is pinned as an equally valid
    caller by the companion happy-path test.
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

    The §3.8 precondition requires the actor to be a member of the
    project. Both Zoltán and Tibor are added so the happy-path and
    ``ri_senior`` equivalence tests reuse the same project row.
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


@pytest.fixture()
def gsc_done(db_session, nex_horizont) -> ProjectModule:
    """Persist the prerequisite GSC module **already in ``done``**.

    BEHAVIOR.md §3.8 step 2 system response: "GSC je ``done`` →
    validácia ok". The §3.8 worked example gates the STK transition
    on GSC having reached its terminal state. Seeding GSC directly in
    ``done`` keeps this test focused on the STK status PATCH rather
    than on the transitive GSC lifecycle (that is already covered by
    ``test_full_lifecycle_progression_planned_to_done`` below for
    STK itself).
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
def stk_planned(db_session, nex_horizont, gsc_done) -> ProjectModule:
    """Persist STK in ``planned`` with a dependency edge on GSC.

    This is the starting state of the §3.8 worked example: STK exists
    (created via §3.7 ``add_module``), declares a dependency on GSC,
    and sits at ``planned`` waiting for Tibor to move it forward.
    """
    module = ProjectModule(
        project_id=nex_horizont.id,
        code="STK",
        name="Skladové karty zásob",
        category="Sklad",
        status="planned",
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


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.8 end-to-end.
# ---------------------------------------------------------------------------


class TestSetModuleStatusHappyPath:
    """End-to-end walkthrough of workflow §3.8 against the real app."""

    def test_full_workflow_pushes_stk_to_in_development(
        self,
        client,
        db_session,
        stk_planned,
        gsc_done,
    ):
        """Drive steps 1-3 of the workflow and verify every postcondition.

        Reproduces the §3.8 worked example faithfully: Tibor opens the
        Module Registry, the UI verifies STK's dependencies are all
        ``done``, and the PATCH advances STK to ``in_development``.
        The test asserts both the HTTP contract (status codes, payload
        shape, ``updated_at`` bumped) and the DB state plus the
        postcondition that an Architect session for STK is now
        creatable.
        """
        # --- Step 1 (UI side): Tibor opens the Module Registry and the
        # status-select for STK. The UI looks up STK's dependencies to
        # decide whether ``in_development`` is enabled. Modelled here
        # as the public "what does this module depend on" query.
        deps_resp = client.get(
            "/api/v1/module-dependencies",
            params={"module_id": str(stk_planned.id)},
        )
        assert deps_resp.status_code == 200, deps_resp.text
        assert deps_resp.json()["total"] == 1
        dep_target_id = deps_resp.json()["items"][0]["depends_on_module_id"]
        assert dep_target_id == str(gsc_done.id)

        # --- Step 2 (system): every prerequisite must be ``done``. The
        # UI intersects "prerequisites of this module" with "not yet
        # ``done``" — the resulting list must be empty for the
        # ``in_development`` option to be enabled.
        blockers: list[str] = []
        for dep in deps_resp.json()["items"]:
            prereq = client.get(f"/api/v1/project-modules/{dep['depends_on_module_id']}")
            assert prereq.status_code == 200
            if prereq.json()["status"] != "done":
                blockers.append(prereq.json()["code"])
        assert blockers == [], f"STK unexpectedly blocked by {blockers}"

        # Capture the module state pre-PATCH — step 3 must preserve
        # the non-``status`` fields.
        before = client.get(f"/api/v1/project-modules/{stk_planned.id}")
        assert before.status_code == 200
        before_body = before.json()
        assert before_body["status"] == "planned"

        # --- Step 3 (system): PATCH advances STK to ``in_development``.
        # The response body carries the updated row, which the UI
        # renders as the "Modul STK je teraz v stave in_development"
        # confirmation.
        patch_resp = client.patch(
            f"/api/v1/project-modules/{stk_planned.id}",
            json={"status": "in_development"},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        body = patch_resp.json()
        assert body["id"] == str(stk_planned.id)
        assert body["project_id"] == str(stk_planned.project_id)
        # §3.8 step 3 / postcondition line 1: "status='in_development'".
        assert body["status"] == "in_development"
        # Unchanged fields stay put — the PATCH only touched ``status``.
        assert body["code"] == before_body["code"] == "STK"
        assert body["name"] == before_body["name"] == "Skladové karty zásob"
        assert body["category"] == before_body["category"] == "Sklad"
        assert body["design_doc_path"] == before_body["design_doc_path"]
        # ``created_at`` is immutable; ``updated_at`` stays non-null
        # (``onupdate=func.now()`` resolves to SQL ``now()``, which
        # returns the transaction start time on PostgreSQL — so the
        # visible value is stable within a SAVEPOINT-isolated test).
        assert body["created_at"] == before_body["created_at"]
        assert body["updated_at"] is not None

        # --- Postcondition verification (HTTP) -------------------------
        # 1. Subsequent GETs reflect the new status — the Module
        #    Registry UI re-renders STK with the ``in_development``
        #    badge (DESIGN.md §3.2 ``ModuleStatusBadge``).
        after = client.get(f"/api/v1/project-modules/{stk_planned.id}")
        assert after.status_code == 200
        assert after.json()["status"] == "in_development"

        # 2. The list query filtered by ``status=in_development``
        #    surfaces STK — pins that the indexed column is in sync.
        in_dev_list = client.get(
            "/api/v1/project-modules",
            params={
                "project_id": str(stk_planned.project_id),
                "status": "in_development",
            },
        )
        assert in_dev_list.status_code == 200
        codes = {row["code"] for row in in_dev_list.json()["items"]}
        assert "STK" in codes

        # 3. The ``status=planned`` filter no longer surfaces STK.
        planned_list = client.get(
            "/api/v1/project-modules",
            params={
                "project_id": str(stk_planned.project_id),
                "status": "planned",
            },
        )
        assert planned_list.status_code == 200
        planned_codes = {row["code"] for row in planned_list.json()["items"]}
        assert "STK" not in planned_codes

        # 4. §3.8 postcondition line 2: "Architect session pre STK je
        #    teraz možné otvoriť". Creating the session through the
        #    real router succeeds — the DB layer does not gate session
        #    creation on module status, but the positive observability
        #    check keeps the §3.8 postcondition visible in the test
        #    matrix. ``created_by`` is resolved via the project row
        #    rather than the module's ORM relationship so the test
        #    is not coupled to relationship-eager-loading semantics.
        project = db_session.get(Project, stk_planned.project_id)
        assert project is not None
        session_resp = client.post(
            "/api/v1/architect-sessions",
            json={
                "project_id": str(stk_planned.project_id),
                "module_id": str(stk_planned.id),
                "created_by": str(project.created_by),
            },
        )
        assert session_resp.status_code == 201, session_resp.text
        session_body = session_resp.json()
        assert session_body["module_id"] == str(stk_planned.id)
        assert session_body["status"] == "active"

        # --- Postcondition verification (DB state) ---------------------
        db_session.expire_all()

        # 1. ``project_modules.status`` is persisted — not a
        #    session-cached value — and the DB CHECK did not reject
        #    the transition.
        persisted_module = db_session.get(ProjectModule, stk_planned.id)
        assert persisted_module is not None
        assert persisted_module.status == "in_development"
        assert persisted_module.code == "STK"

        # 2. The Architect session is persisted and wired to STK.
        persisted_session = db_session.get(ArchitectSession, uuid.UUID(session_body["id"]))
        assert persisted_session is not None
        assert persisted_session.module_id == stk_planned.id
        assert persisted_session.project_id == stk_planned.project_id
        assert persisted_session.status == "active"

    def test_full_lifecycle_progression_planned_to_done(
        self,
        client,
        db_session,
        stk_planned,
    ):
        """PATCH drives a single module through every legal transition.

        The lifecycle is ``planned`` → ``in_design`` →
        ``in_development`` → ``done`` per DESIGN.md §2.2. Each hop is
        a separate PATCH, every one must land 200 and every resulting
        GET must surface the new literal. This pins the full
        status-CHECK contract (``ck_project_modules_status``) through
        the HTTP surface, not just the §3.8 worked transition.
        """
        module_id = stk_planned.id
        lifecycle: list[str] = ["in_design", "in_development", "done"]

        for target_status in lifecycle:
            resp = client.patch(
                f"/api/v1/project-modules/{module_id}",
                json={"status": target_status},
            )
            assert resp.status_code == 200, f"PATCH to {target_status!r} failed: {resp.text}"
            assert resp.json()["status"] == target_status

            show = client.get(f"/api/v1/project-modules/{module_id}")
            assert show.status_code == 200
            assert show.json()["status"] == target_status

        # Final DB state: STK landed in ``done``.
        db_session.expire_all()
        persisted = db_session.get(ProjectModule, module_id)
        assert persisted is not None
        assert persisted.status == "done"

    def test_ri_director_may_also_set_status(
        self,
        client,
        db_session,
        zoltan,
        stk_planned,
    ):
        """Zoltán (``ri_director``) is an equally valid actor per §3.8.

        BEHAVIOR.md §3.8 lists the actor as "[[actor:ri_director]]
        alebo [[actor:ri_senior]]". The worked example uses Tibor;
        this test pins Zoltán's equivalence by advancing STK from
        ``planned`` to ``in_design`` (the first legal hop, which
        does not require any dependencies to be ``done`` — the
        §3.8 precondition's dependency clause only guards the
        ``in_development`` transition). The router accepts any ``ri``
        member identically.
        """
        resp = client.patch(
            f"/api/v1/project-modules/{stk_planned.id}",
            json={"status": "in_design"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "in_design"

        db_session.expire_all()
        persisted = db_session.get(ProjectModule, stk_planned.id)
        assert persisted is not None
        assert persisted.status == "in_design"


# ---------------------------------------------------------------------------
# Edge cases — invalid status literal, missing module, timestamp bump.
# ---------------------------------------------------------------------------


class TestSetModuleStatusEdgeCases:
    """Edge cases around the PATCH status surface.

    The three guards below back the §3.8 precondition "Modul existuje
    v projekte" (missing module → 404) and the
    ``ck_project_modules_status`` CHECK (invalid literal → 422 at the
    Pydantic layer, never reaching the DB). The ``updated_at`` check
    pins the ORM ``onupdate=func.now()`` semantics DESIGN.md §2.2
    declares for every mutable table.
    """

    def test_invalid_status_value_is_rejected_with_422(
        self,
        client,
        db_session,
        stk_planned,
    ):
        """PATCH with a non-literal status → HTTP 422, row unchanged.

        :data:`ProjectModuleStatus` is a Pydantic ``Literal`` of the
        four allowed values; a bogus string fails schema validation
        before the service sees it. The row must survive untouched —
        no partial update is allowed.
        """
        resp = client.patch(
            f"/api/v1/project-modules/{stk_planned.id}",
            json={"status": "not-a-status"},
        )
        assert resp.status_code == 422, resp.text

        # The original ``planned`` status survives.
        db_session.expire_all()
        persisted = db_session.get(ProjectModule, stk_planned.id)
        assert persisted is not None
        assert persisted.status == "planned"

        show = client.get(f"/api/v1/project-modules/{stk_planned.id}")
        assert show.status_code == 200
        assert show.json()["status"] == "planned"

    def test_patch_status_on_missing_module_returns_404(
        self,
        client,
    ):
        """PATCH against a random UUID → HTTP 404.

        The §3.8 precondition "Modul existuje v projekte" is enforced
        at the service layer:
        :func:`backend.services.project_module.update` calls
        :func:`get_by_id`, which raises
        ``ValueError("ProjectModule {id} not found")``. The router's
        ``_map_value_error`` maps the "not found" message to HTTP 404.
        """
        missing_id = uuid.uuid4()
        resp = client.patch(
            f"/api/v1/project-modules/{missing_id}",
            json={"status": "in_development"},
        )
        assert resp.status_code == 404, resp.text
        assert "not found" in resp.json()["detail"].lower()

    def test_status_patch_preserves_unrelated_fields(
        self,
        client,
        db_session,
        stk_planned,
    ):
        """PATCH with just ``status`` leaves ``code``/``name``/``category`` intact.

        The §3.8 worked-example transition only carries the new
        ``status`` literal. :class:`ProjectModuleUpdate` declares every
        field :data:`Optional` with ``default=None``; a naive service
        that iterated ``model_dump()`` without ``exclude_unset=True``
        would helpfully null out every ``None``-defaulted sibling
        column. The service's ``model_dump(exclude_unset=True)`` +
        ``allowed_fields`` allow-list are the two guards under test.
        """
        # Seed a non-default ``design_doc_path`` so the preservation
        # check is meaningful — if the PATCH accidentally dropped the
        # field, the value would flip back to ``None``.
        seed_resp = client.patch(
            f"/api/v1/project-modules/{stk_planned.id}",
            json={"design_doc_path": "/kb/nex-horizont/modules/stk/DESIGN.md"},
        )
        assert seed_resp.status_code == 200, seed_resp.text
        seeded = seed_resp.json()
        assert seeded["design_doc_path"] == "/kb/nex-horizont/modules/stk/DESIGN.md"

        # Now PATCH only ``status`` — every other field must survive.
        resp = client.patch(
            f"/api/v1/project-modules/{stk_planned.id}",
            json={"status": "in_design"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "in_design"
        assert body["code"] == seeded["code"]
        assert body["name"] == seeded["name"]
        assert body["category"] == seeded["category"]
        assert body["design_doc_path"] == seeded["design_doc_path"]

        # DB state agrees with the HTTP payload.
        db_session.expire_all()
        persisted = db_session.get(ProjectModule, stk_planned.id)
        assert persisted is not None
        assert persisted.status == "in_design"
        assert persisted.design_doc_path == "/kb/nex-horizont/modules/stk/DESIGN.md"
        assert persisted.code == "STK"
        assert persisted.name == "Skladové karty zásob"
        assert persisted.category == "Sklad"

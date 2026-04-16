"""Integration test for BEHAVIOR.md §3.6 ``workflow:create_project``.

Exercises the full happy path of the **create_project** workflow
end-to-end through the real FastAPI ``app``. The workflow is Zoltán
(``ri_director`` per BEHAVIOR.md §1.1) — or any ``ri``-role user —
creating a brand-new project in NEX Studio from the dashboard. The
worked example (§3.6 "Konkrétny príklad") is the creation of the NEX
Horizont project with slug ``nex-horizont``, category ``multimodule``,
ports 9170/9171/9172, repo ``rauschiccsk/nex-horizont`` and all four
ICC members (Zoltán, Tibor, Dominik, Nazar) added as
:class:`ProjectMember` rows.

    Precondition (per BEHAVIOR.md §3.6):
        * Actor has role ``ri`` (``ri_director`` Zoltán or
          ``ri_senior`` Tibor per BEHAVIOR.md §1.1).
        * The project ``name`` and derived ``slug`` do not yet exist in
          the system.

    Steps (per BEHAVIOR.md §3.6):
        1. Zoltán clicks "Nový projekt" on the dashboard — the UI
           surfaces the form. Not HTTP-observable; modelled via the
           "no existing project with this name / slug" list query the
           dashboard uses to detect duplicates client-side.
        2. Zoltán fills in Name, Category, Description, Repo URL,
           ports, Members — client-side only, no HTTP round-trip.
        3. Zoltán clicks "Vytvoriť projekt" → the orchestrator drives
           the multi-step creation sequence:
                a. ``POST /api/v1/projects`` with the full payload →
                   the service validates uniqueness of ``name`` and
                   ``slug`` and persists the row with
                   ``status='active'`` and the caller's ``created_by``.
                b. ``POST /api/v1/project-members`` per selected member
                   — four POSTs in the §3.6 worked example — each
                   validated against ``UNIQUE(project_id, user_id)``.
                   The creator is automatically a member and is added
                   as the first row (step 4 post-condition "Creator je
                   automaticky člen").
                c. ``POST /api/v1/report-configs`` with just
                   ``project_id`` → the DB server defaults fill in the
                   senior / junior hourly rates (75 EUR / 35 EUR per
                   BEHAVIOR.md §3.6 postcondition line 4).
        4. — (system) — project and membership / report-config rows
           are persisted; the HTTP layer returns the ``Project`` row.
        5. System redirects to the project dashboard — modelled here
           as a ``GET /api/v1/projects/{id}`` the dashboard fires on
           load to hydrate the ``ProjectPage``.

    Postcondition (per BEHAVIOR.md §3.6):
        * ``projects`` row exists with a unique ``slug`` and
          ``status='active'`` / ``category='multimodule'``.
        * ``project_members`` contains one row per selected member
          (including the creator).
        * ``report_configs`` row exists with the default senior / junior
          hourly rates (75 EUR / 35 EUR).
        * Guardian is disabled by default (``guardian_enabled=False``)
          — BEHAVIOR.md §3.6 "Konkrétny príklad": "Guardian je vypnutý
          (default), Zoltán ho neskôr zapne".

Edge cases verified alongside the happy path:

    * **Duplicate name** — Zoltán re-submits a payload whose ``name``
      collides with an existing project. The service surfaces the
      collision as ``ValueError`` and the router translates it to
      HTTP 409. Nothing is persisted and the list endpoint still
      returns exactly one project under that name (the original).
    * **Duplicate slug** — the form in the dashboard derives the slug
      from the name client-side, but a direct API hit could still
      collide on slug while using a different name. The router must
      reject it with HTTP 409.
    * **``ri_senior`` may also create a project** — BEHAVIOR.md §3.6
      lists the actor as "[[actor:ri_director]] alebo
      [[actor:ri_senior]]" (both resolve to ``role='ri'``). Tibor is
      pinned as an equally valid creator.

Auth note:
    The current codebase (Feats 0–6) wires routers directly without a
    JWT dependency, so the integration test does not exercise a login
    flow. The "role=ri" precondition is satisfied by persisting the
    creator with ``role='ri'`` and passing ``created_by=<uuid>`` on
    the payload. Role enforcement at the router level is a separate
    concern covered by future auth-middleware tests.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project, ProjectMember
from backend.db.models.reports import ReportConfig

# ---------------------------------------------------------------------------
# Precondition fixtures — the four ICC users named in the §3.6 worked
# example (Zoltán, Tibor, Dominik, Nazar). Zoltán and Tibor are ``ri``
# so either may act as the creator; Dominik (``ha``) and Nazar
# (``shu``) are members only.
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
    """Persist Tibor — the ``ri_senior`` actor from BEHAVIOR.md §1.1."""
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
    """Persist Dominik — the ``ha`` (senior developer) actor per BEHAVIOR.md §1.1."""
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
    """Persist Nazar — the ``shu`` (junior developer) actor per BEHAVIOR.md §1.1."""
    user = User(
        username="nazar",
        email="nazar@isnex.ai",
        password_hash="hashed-placeholder",
        role="shu",
    )
    db_session.add(user)
    db_session.flush()
    return user


# ---------------------------------------------------------------------------
# Helpers — build payloads that mirror the §3.6 worked example.
# ---------------------------------------------------------------------------


# BEHAVIOR.md §3.6 step 2: "Názov='NEX Horizont', Kategória='multimodule',
# Repo='rauschiccsk/nex-horizont', Backend port=9170, Frontend port=9171,
# DB port=9172".
NEX_HORIZONT_NAME = "NEX Horizont"
NEX_HORIZONT_SLUG = "nex-horizont"
NEX_HORIZONT_REPO_URL = "rauschiccsk/nex-horizont"
NEX_HORIZONT_BACKEND_PORT = 9170
NEX_HORIZONT_FRONTEND_PORT = 9171
NEX_HORIZONT_DB_PORT = 9172
NEX_HORIZONT_DESCRIPTION = "Enterprise ERP successor to NEX Command."


def _project_payload(creator_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    """Build a JSON payload for ``POST /api/v1/projects``.

    Defaults mirror the §3.6 worked example exactly (NEX Horizont,
    multimodule, three ports, ANDROS-style repo slug). Overrides let
    individual tests swap fields (e.g. duplicate ``name`` / ``slug``
    for the collision edge cases).
    """
    payload: dict[str, Any] = {
        "name": NEX_HORIZONT_NAME,
        "slug": NEX_HORIZONT_SLUG,
        "category": "multimodule",
        "description": NEX_HORIZONT_DESCRIPTION,
        "repo_url": NEX_HORIZONT_REPO_URL,
        "backend_port": NEX_HORIZONT_BACKEND_PORT,
        "frontend_port": NEX_HORIZONT_FRONTEND_PORT,
        "db_port": NEX_HORIZONT_DB_PORT,
        "created_by": str(creator_id),
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Happy path — BEHAVIOR.md §3.6 end-to-end.
# ---------------------------------------------------------------------------


class TestCreateProjectHappyPath:
    """End-to-end walkthrough of workflow §3.6 against the real app."""

    def test_full_workflow_with_four_members_and_default_report_config(
        self,
        client,
        db_session,
        zoltan,
        tibor,
        dominik,
        nazar,
    ):
        """Drive steps 1-5 of the workflow and verify every postcondition.

        The test asserts both the HTTP contract (status codes, payload
        shape) and the database state after each step. The worked
        example from BEHAVIOR.md §3.6 is reproduced faithfully: Zoltán
        creates NEX Horizont with four members and the default report
        configuration is created automatically.
        """
        # --- Step 1 (precondition): no project with the target name /
        # slug exists yet. The dashboard uses this list to warn about
        # duplicates client-side; the test uses it as the §3.6
        # precondition check.
        initial_list = client.get("/api/v1/projects")
        assert initial_list.status_code == 200, initial_list.text
        assert initial_list.json()["total"] == 0
        assert initial_list.json()["items"] == []

        # --- Steps 2-3a: create the project itself. Validation lives
        # in the service: unique ``name``, unique ``slug``. The DB
        # fills in ``id``, ``created_at``, ``updated_at``; ``status``
        # defaults to ``active`` and ``guardian_enabled`` to ``False``
        # — both per BEHAVIOR.md §3.6 postcondition.
        create_resp = client.post(
            "/api/v1/projects",
            json=_project_payload(zoltan.id),
        )
        assert create_resp.status_code == 201, create_resp.text
        created = create_resp.json()
        assert created["name"] == NEX_HORIZONT_NAME
        assert created["slug"] == NEX_HORIZONT_SLUG
        assert created["category"] == "multimodule"
        assert created["description"] == NEX_HORIZONT_DESCRIPTION
        assert created["repo_url"] == NEX_HORIZONT_REPO_URL
        assert created["backend_port"] == NEX_HORIZONT_BACKEND_PORT
        assert created["frontend_port"] == NEX_HORIZONT_FRONTEND_PORT
        assert created["db_port"] == NEX_HORIZONT_DB_PORT
        assert created["status"] == "active"  # §3.6 postcondition line 3.
        # "Guardian je vypnutý (default)" — §3.6 Konkrétny príklad.
        assert created["guardian_enabled"] is False
        assert created["created_by"] == str(zoltan.id)
        assert created["id"]
        assert created["created_at"]
        assert created["updated_at"]

        project_id = created["id"]

        # --- Step 3b: add all four selected members. The creator is
        # automatically the first — BEHAVIOR.md §3.6 step 4 "Creator
        # je automaticky člen". ``UNIQUE(project_id, user_id)`` is
        # enforced by the service; a duplicate surfaces as HTTP 409.
        selected_members = [zoltan, tibor, dominik, nazar]
        member_ids: list[str] = []
        for member in selected_members:
            resp = client.post(
                "/api/v1/project-members",
                json={
                    "project_id": project_id,
                    "user_id": str(member.id),
                },
            )
            assert resp.status_code == 201, resp.text
            assert resp.json()["project_id"] == project_id
            assert resp.json()["user_id"] == str(member.id)
            member_ids.append(resp.json()["id"])

        # --- Step 3c: create the default report configuration. Only
        # ``project_id`` is sent — the Pydantic schema / DB
        # ``server_default`` fills in the canonical 75 EUR / 35 EUR
        # rates (BEHAVIOR.md §3.6 postcondition line 4).
        report_cfg_resp = client.post(
            "/api/v1/report-configs",
            json={"project_id": project_id},
        )
        assert report_cfg_resp.status_code == 201, report_cfg_resp.text
        cfg = report_cfg_resp.json()
        assert cfg["project_id"] == project_id
        assert Decimal(cfg["senior_hourly_rate_eur"]) == Decimal("75.0000")
        assert Decimal(cfg["junior_hourly_rate_eur"]) == Decimal("35.0000")

        # --- Step 5: system redirects to project dashboard; the
        # dashboard fires a ``GET /api/v1/projects/{id}`` on load to
        # hydrate the ``ProjectPage``. The re-read must echo what the
        # UI will show — every field round-trips.
        show_resp = client.get(f"/api/v1/projects/{project_id}")
        assert show_resp.status_code == 200
        assert show_resp.json()["id"] == project_id
        assert show_resp.json()["slug"] == NEX_HORIZONT_SLUG
        assert show_resp.json()["status"] == "active"
        assert show_resp.json()["category"] == "multimodule"

        # --- Postcondition verification (HTTP) -------------------------
        # 1. The project list shows exactly one project now.
        after_list = client.get("/api/v1/projects")
        assert after_list.status_code == 200
        assert after_list.json()["total"] == 1
        assert [row["id"] for row in after_list.json()["items"]] == [project_id]

        # 2. Four memberships exist for the project.
        members_list = client.get(
            "/api/v1/project-members",
            params={"project_id": project_id},
        )
        assert members_list.status_code == 200
        assert members_list.json()["total"] == 4
        listed_user_ids = {row["user_id"] for row in members_list.json()["items"]}
        assert listed_user_ids == {
            str(zoltan.id),
            str(tibor.id),
            str(dominik.id),
            str(nazar.id),
        }

        # 3. The report configuration for the project is retrievable
        #    and carries the default rates.
        cfg_list = client.get(
            "/api/v1/report-configs",
            params={"project_id": project_id},
        )
        assert cfg_list.status_code == 200
        assert cfg_list.json()["total"] == 1
        assert Decimal(cfg_list.json()["items"][0]["senior_hourly_rate_eur"]) == Decimal("75.0000")
        assert Decimal(cfg_list.json()["items"][0]["junior_hourly_rate_eur"]) == Decimal("35.0000")

        # --- Postcondition verification (DB state) ---------------------
        db_session.expire_all()

        # 1. ``projects`` row exists with unique slug and the §3.6
        #    postcondition fields.
        persisted_project = db_session.get(Project, uuid.UUID(project_id))
        assert persisted_project is not None
        assert persisted_project.slug == NEX_HORIZONT_SLUG
        assert persisted_project.status == "active"
        assert persisted_project.category == "multimodule"
        assert persisted_project.created_by == zoltan.id
        # §3.6 Konkrétny príklad: "Guardian je vypnutý (default)".
        assert persisted_project.guardian_enabled is False
        # The ports from step 2 are preserved verbatim.
        assert persisted_project.backend_port == NEX_HORIZONT_BACKEND_PORT
        assert persisted_project.frontend_port == NEX_HORIZONT_FRONTEND_PORT
        assert persisted_project.db_port == NEX_HORIZONT_DB_PORT
        assert persisted_project.repo_url == NEX_HORIZONT_REPO_URL

        # 2. ``project_members`` has one row per selected member
        #    (creator included).
        persisted_members = [db_session.get(ProjectMember, uuid.UUID(mid)) for mid in member_ids]
        assert all(row is not None for row in persisted_members)
        assert {row.user_id for row in persisted_members} == {
            zoltan.id,
            tibor.id,
            dominik.id,
            nazar.id,
        }
        assert all(row.project_id == persisted_project.id for row in persisted_members)

        # 3. ``report_configs`` has one row for the project with the
        #    default senior / junior hourly rates (75 / 35 EUR).
        cfg_id = cfg_list.json()["items"][0]["id"]
        persisted_cfg = db_session.get(ReportConfig, uuid.UUID(cfg_id))
        assert persisted_cfg is not None
        assert persisted_cfg.project_id == persisted_project.id
        assert persisted_cfg.senior_hourly_rate_eur == Decimal("75.0000")
        assert persisted_cfg.junior_hourly_rate_eur == Decimal("35.0000")

    def test_ri_senior_may_also_create_a_project(
        self,
        client,
        db_session,
        tibor,
    ):
        """``ri_senior`` (Tibor) is an equally valid creator per §3.6.

        BEHAVIOR.md §3.6 lists the actor as "[[actor:ri_director]]
        alebo [[actor:ri_senior]]" — both roles resolve to
        ``role='ri'`` at the DB level. The router accepts any user
        with that role as ``created_by``; enforcement is identical
        regardless of which specific ``ri`` user clicks "Vytvoriť
        projekt". Zoltán is the worked-example actor covered above;
        this test pins Tibor's equivalence.
        """
        resp = client.post(
            "/api/v1/projects",
            json=_project_payload(
                tibor.id,
                name="NEX Marina",
                slug="nex-marina",
                description="Marina booking — singlemodule sibling.",
                category="singlemodule",
            ),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["created_by"] == str(tibor.id)
        assert resp.json()["slug"] == "nex-marina"
        assert resp.json()["category"] == "singlemodule"
        # Defaults stay intact regardless of who creates the row.
        assert resp.json()["status"] == "active"
        assert resp.json()["guardian_enabled"] is False

        db_session.expire_all()
        persisted = db_session.get(Project, uuid.UUID(resp.json()["id"]))
        assert persisted is not None
        assert persisted.created_by == tibor.id


# ---------------------------------------------------------------------------
# Edge cases — duplicate name / slug.
# ---------------------------------------------------------------------------


class TestCreateProjectEdgeCases:
    """Edge cases around the workflow's uniqueness preconditions.

    BEHAVIOR.md §3.6 step 3 spells out the validation contract: "Systém
    validuje: unikátny názov a slug, validné porty, existujúci repo
    slug". The two uniqueness cheques (``name`` and ``slug``) translate
    directly into HTTP 409 at the router layer via the service-layer
    ``ValueError`` translation (``_map_value_error`` in
    :mod:`backend.api.routes.projects`). The happy path already pins
    "validné porty" implicitly (the ports round-trip through the DB
    without constraint violation); the "existujúci repo slug" check is
    external to the API surface (GitHub org lookup) and is not HTTP-
    observable here.
    """

    def test_duplicate_name_is_rejected_with_409(self, client, db_session, zoltan):
        """POST with a name that collides with an existing project → 409.

        BEHAVIOR.md §3.6 step 3 "unikátny názov". The service layer
        validates uniqueness pre-flush and raises :class:`ValueError`
        (``... already exists``), which the router's ``_map_value_error``
        translates to HTTP 409. The second POST must not create a
        second row.
        """
        first = client.post(
            "/api/v1/projects",
            json=_project_payload(zoltan.id),
        )
        assert first.status_code == 201, first.text

        # Same name, different slug — still rejected on the name check.
        conflict = client.post(
            "/api/v1/projects",
            json=_project_payload(zoltan.id, slug="nex-horizont-2"),
        )
        assert conflict.status_code == 409, conflict.text
        assert "already exists" in conflict.json()["detail"].lower()

        # Only the first row survives.
        db_session.expire_all()
        listing = client.get("/api/v1/projects", params={"status": "active"})
        assert listing.status_code == 200
        assert listing.json()["total"] == 1
        assert listing.json()["items"][0]["slug"] == NEX_HORIZONT_SLUG

    def test_duplicate_slug_is_rejected_with_409(self, client, db_session, zoltan):
        """POST with a slug that collides with an existing project → 409.

        BEHAVIOR.md §3.6 step 3 "unikátny … slug". The dashboard
        derives the slug from the name client-side, but a direct API
        hit can still collide on slug while using a different name.
        The router must reject it with HTTP 409 and no row is
        created.
        """
        first = client.post(
            "/api/v1/projects",
            json=_project_payload(zoltan.id),
        )
        assert first.status_code == 201, first.text

        # Different name, same slug — rejected on the slug check.
        conflict = client.post(
            "/api/v1/projects",
            json=_project_payload(
                zoltan.id,
                name="NEX Horizont (Mirror)",
                slug=NEX_HORIZONT_SLUG,
            ),
        )
        assert conflict.status_code == 409, conflict.text
        assert "already exists" in conflict.json()["detail"].lower()

        # Only the first row survives; filtering by the duplicated
        # slug's project id returns the original.
        db_session.expire_all()
        persisted = db_session.get(Project, uuid.UUID(first.json()["id"]))
        assert persisted is not None
        assert persisted.name == NEX_HORIZONT_NAME

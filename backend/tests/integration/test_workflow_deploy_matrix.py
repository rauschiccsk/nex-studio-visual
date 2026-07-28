"""Integration tests for the UAT & PROD tabs — version × customer matrix (CR-V2-027).

Exercises the per-customer deploy surface (design §3.3/§3.4/§3.5) end-to-end
through the real FastAPI ``app``: the ``/projects/{slug}/deploy-matrix`` read
that feeds both tabs, the Nasadiť (deploy) action, the Akceptovať (UAT
acceptance) action, and the never-bypassed PROD acceptance gate.

The deploy runner (real ``uat_provisioner`` + docker compose up) is faked so no
``git``/``docker`` is spawned — the module-level ``_default_deploy_runner`` is
monkeypatched (the route never injects a runner, per the deploy service
contract).

Safety invariants asserted here (the CR's load-bearing rules):
  * **The acceptance gate is NEVER bypassed (§3.5, incident 2026-06-10).** A PROD
    deploy of an un-accepted (version, customer) is rejected with 409, and the
    matrix exposes ``accepted_versions`` so the FE keeps PROD Nasadiť disabled
    until acceptance.
  * **Akceptovať LOGS who/when/version/customer (§3.5).** The accept event
    records the actor, the version, the customer and a timestamp.
  * **Different customers may run different versions simultaneously (§3.3).**
  * **No secret material is ever returned (§4/OQ-5).** No response field carries
    a secret.
"""

from __future__ import annotations

import uuid as _uuid

import bcrypt
import pytest
from sqlalchemy import select

from backend.db.models.customers import Customer
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import deploy as deploy_service
from backend.services import orchestrator

# ---------------------------------------------------------------------------
# Fixtures — a project with two customers and two verified versions
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_deploy_runner(monkeypatch):
    """Replace the real provision+up runner with an in-memory success stub.

    Returns ``(ok=True, detail, url)`` without spawning git/docker. The recorded
    ``calls`` list lets a test assert the runner was invoked with the expected
    (preserve-by-default) parameters.
    """
    calls: list[dict] = []

    async def _runner(*, project_slug, uat_slug, version_number, force_fresh, admin_password=None):
        calls.append(
            {
                "project_slug": project_slug,
                "uat_slug": uat_slug,
                "version_number": version_number,
                "force_fresh": force_fresh,
            }
        )
        return True, "OK (faked)", f"https://uat-{uat_slug}.isnex.eu"

    monkeypatch.setattr(deploy_service, "_default_deploy_runner", _runner)
    return calls


@pytest.fixture()
def prod_failing_deploy_runner(monkeypatch):
    """A runner that SUCCEEDS for UAT but FAILS for PROD (the ``-prod`` instance slug).

    Lets a test drive the UAT deploy + acceptance normally, then exercise a FAILED first-PROD
    deploy — so the §3.6 graduation (gated on ``first_prod and ok``) is forced to leave the version
    un-promoted. ``git``/``docker`` are never spawned. The ``calls`` list records each invocation.
    """
    calls: list[dict] = []

    async def _runner(*, project_slug, uat_slug, version_number, force_fresh, admin_password=None):
        calls.append(
            {
                "project_slug": project_slug,
                "uat_slug": uat_slug,
                "version_number": version_number,
                "force_fresh": force_fresh,
            }
        )
        if uat_slug.endswith("-prod"):
            return False, "provision failed (faked)", None
        return True, "OK (faked)", f"https://uat-{uat_slug}.isnex.eu"

    monkeypatch.setattr(deploy_service, "_default_deploy_runner", _runner)
    return calls


def _seed_project(db, *, creator: User) -> Project:
    suffix = _uuid.uuid4().hex[:8]
    project = Project(
        name=f"Deploy Matrix Proj {suffix}",
        slug=f"deploy-matrix-{suffix}",
        type="standard",
        auth_mode="password",
        description="CR-V2-027 deploy matrix test project.",
        created_by=creator.id,
    )
    db.add(project)
    db.flush()
    return project


def _seed_verified_version(db, project: Project, version_number: str) -> Version:
    """A version carried to Hotovo (``current_stage='done'``) = VERIFIED (§3.1)."""
    version = Version(project_id=project.id, version_number=version_number, name=version_number)
    db.add(version)
    db.flush()
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="done",
            current_actor="auditor",
            status="done",
            next_action="",
        )
    )
    db.flush()
    # CR-V2-056: verified is COMPUTED from the Verifikácia PASS verdict (version_verified), not the stored
    # 'done' stage alone — record the PASS. No verified_sha in the test repo → 'unbound' → verified.
    orchestrator._record_message(
        db,
        version_id=version.id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content="PASS",
        payload={"verdict": "PASS", "phase": "verifikacia"},
    )
    db.flush()
    return version


def _seed_unverified_version(db, project: Project, version_number: str) -> Version:
    """A version still in-flight (Programovanie) — NOT deployable."""
    version = Version(project_id=project.id, version_number=version_number, name=version_number)
    db.add(version)
    db.flush()
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="programovanie",
            current_actor="ai_agent",
            status="agent_working",
            next_action="",
        )
    )
    db.flush()
    return version


def _seed_customer(db, project: Project, slug: str) -> Customer:
    customer = Customer(project_id=project.id, name=slug.upper(), slug=slug, subdomain=slug)
    db.add(customer)
    db.flush()
    return customer


def _current_user(db) -> User:
    """The ri user the conftest ``client`` fixture authenticates as.

    The conftest seeds its own ri user and overrides the gates to it; for the
    actor assertion we read the most recently created ri user back from the DB.
    """
    rows = db.query(User).filter(User.role == "ri").order_by(User.created_at.desc()).all()
    return rows[0]


# ---------------------------------------------------------------------------
# Matrix read — verified versions + per-customer cells
# ---------------------------------------------------------------------------


class TestDeployMatrixRead:
    def test_matrix_lists_only_verified_versions(self, client, db_session, fake_deploy_runner):
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        _seed_verified_version(db_session, project, "v0.1.0")
        _seed_verified_version(db_session, project, "v0.2.0")
        _seed_unverified_version(db_session, project, "v0.3.0")  # in-flight → excluded
        _seed_customer(db_session, project, "andros")

        resp = client.get(f"/api/v1/projects/{project.slug}/deploy-matrix")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Only the two Hotovo versions are deployable; the in-flight one is excluded.
        assert body["verified_versions"] == ["v0.2.0", "v0.1.0"]
        assert "v0.3.0" not in body["verified_versions"]
        assert len(body["rows"]) == 1
        row = body["rows"][0]
        assert row["customer_slug"] == "andros"
        # Never deployed yet → empty cells, gate closed.
        assert row["uat_version"] is None
        assert row["prod_version"] is None
        assert row["accepted_versions"] == []
        assert row["uat_url"] is None
        # No secret material in any field.
        assert "secret" not in body and all("secret" not in r for r in body["rows"])

    def test_matrix_flags_a_failed_deploy_attempt(self, client, db_session, prod_failing_deploy_runner):
        """Audit #5: a FAILED newest attempt is SURFACED in the matrix, never hidden behind the last-good cell.

        ``current_version`` returns only the last SUCCESSFUL version, so without an explicit flag a failed
        upgrade reads as a green (or empty) cell. The row must carry ``*_last_attempt_failed`` so the manager
        sees the deploy didn't land.
        """
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        _seed_verified_version(db_session, project, "v0.1.0")
        customer = _seed_customer(db_session, project, "andros")

        # UAT succeeds → last-good version, no failure flag.
        client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "uat"},
        )
        client.post(f"/api/v1/customers/{customer.id}/accept", json={"version_number": "v0.1.0"})
        # PROD FAILS (the fixture fails the ``-prod`` instance).
        prod = client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "prod"},
        )
        assert prod.json()["ok"] is False

        row = client.get(f"/api/v1/projects/{project.slug}/deploy-matrix").json()["rows"][0]
        # UAT succeeded → shows the version, no failure flag.
        assert row["uat_version"] == "v0.1.0"
        assert row["uat_last_attempt_failed"] is False
        # PROD never succeeded AND the newest attempt failed → honest failure flag, not a silent empty cell.
        assert row["prod_version"] is None
        assert row["prod_last_attempt_failed"] is True

    def test_matrix_404_for_unknown_project(self, client):
        resp = client.get("/api/v1/projects/does-not-exist/deploy-matrix")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# The never-bypassed PROD acceptance gate (§3.5) — the CR safety invariant
# ---------------------------------------------------------------------------


class TestProdAcceptanceGate:
    def test_prod_deploy_blocked_until_uat_accepted(self, client, db_session, fake_deploy_runner):
        """⚠ SAFETY INVARIANT: no PROD deploy without a recorded UAT acceptance."""
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        version = _seed_verified_version(db_session, project, "v0.1.0")
        version_id = version.id  # the row that will be graduated IN PLACE (§3.6)
        customer = _seed_customer(db_session, project, "andros")

        # 1) Deploy to UAT — allowed (no gate on UAT).
        uat = client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "uat"},
        )
        assert uat.status_code == 200, uat.text
        assert uat.json()["ok"] is True

        # 2) PROD deploy of the SAME version BEFORE acceptance → BLOCKED (409).
        blocked = client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "prod"},
        )
        assert blocked.status_code == 409, blocked.text
        assert "accept" in blocked.json()["detail"].lower()

        # The matrix still shows the version as NOT accepted → FE keeps PROD disabled.
        matrix = client.get(f"/api/v1/projects/{project.slug}/deploy-matrix").json()
        row = matrix["rows"][0]
        assert "v0.1.0" not in row["accepted_versions"]
        assert row["uat_version"] == "v0.1.0"
        assert row["uat_url"] is not None  # UAT deployed → link present

        # 3) Akceptovať the UAT — opens PROD.
        accept = client.post(
            f"/api/v1/customers/{customer.id}/accept",
            json={"version_number": "v0.1.0"},
        )
        assert accept.status_code == 200, accept.text

        # 4) Now the matrix shows it accepted → FE enables PROD Nasadiť.
        matrix2 = client.get(f"/api/v1/projects/{project.slug}/deploy-matrix").json()
        assert "v0.1.0" in matrix2["rows"][0]["accepted_versions"]

        # 5) PROD deploy now SUCCEEDS (gate satisfied) — and graduates to v1.0.0 (§3.6).
        prod = client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "prod"},
        )
        assert prod.status_code == 200, prod.text
        result = prod.json()
        assert result["ok"] is True
        assert result["bumped_to"] == "v1.0.0"  # first PROD deploy bump (§3.6)

        # 5b) §3.6 graduation is IN PLACE: the BUILT version (v0.1.0) is promoted to v1.0.0 on the
        # SAME row (its history preserved) + marked released — NOT a new empty v1.0.0 shell beside it.
        db_session.expire_all()  # drop identity-map snapshots so we read the committed state
        rows = db_session.execute(select(Version).where(Version.project_id == project.id)).scalars().all()
        assert len(rows) == 1, "graduation must promote in place, not create a second version row"
        graduated = rows[0]
        assert graduated.id == version_id  # SAME row — not a new shell
        assert graduated.version_number == "v1.0.0"
        assert graduated.status == "released"  # a first-prod graduation IS the release
        assert graduated.release_date is not None
        # History preserved: the pipeline_message seeded on the pre-graduation version is still
        # reachable under the SAME version.id after the in-place rename.
        from backend.db.models.pipeline import PipelineMessage

        child = (
            db_session.execute(select(PipelineMessage).where(PipelineMessage.version_id == version_id))
            .scalars()
            .first()
        )
        assert child is not None

    def test_accept_logs_who_when_version_customer(self, client, db_session, fake_deploy_runner):
        """Akceptovať records who/when/version/customer (§3.5 audit log)."""
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        _seed_verified_version(db_session, project, "v0.1.0")
        customer = _seed_customer(db_session, project, "icc")

        client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "uat"},
        )
        accept = client.post(
            f"/api/v1/customers/{customer.id}/accept",
            json={"version_number": "v0.1.0"},
        )
        assert accept.status_code == 200, accept.text
        event = accept.json()
        assert event["event_type"] == "accept"
        assert event["version_number"] == "v0.1.0"  # which version
        assert event["customer_id"] == str(customer.id)  # which customer
        assert event["actor_id"] == str(user.id)  # WHO accepted
        assert event["created_at"] is not None  # WHEN
        assert event["status"] == "ok"

    def test_cannot_accept_version_never_deployed_to_uat(self, client, db_session, fake_deploy_runner):
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        _seed_verified_version(db_session, project, "v0.1.0")
        customer = _seed_customer(db_session, project, "andros")

        # No UAT deploy yet → accept must be rejected (cannot accept the un-deployed).
        accept = client.post(
            f"/api/v1/customers/{customer.id}/accept",
            json={"version_number": "v0.1.0"},
        )
        assert accept.status_code == 409, accept.text

    def test_second_prod_deploy_of_different_version_does_not_regraduate(self, client, db_session, fake_deploy_runner):
        """Only the FIRST prod deploy graduates (§3.6 ``project_had_prod_deploy`` guard).

        After v0.1.0 graduates IN PLACE to v1.0.0, a later prod deploy of a *different*
        version must NOT re-graduate: it deploys under its own number, does not bump, and
        leaves its own row untouched (so the graduated v1.0.0 and the second version coexist).
        """
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        first = _seed_verified_version(db_session, project, "v0.1.0")
        first_id = first.id
        second = _seed_verified_version(db_session, project, "v0.2.0")
        second_id = second.id
        customer = _seed_customer(db_session, project, "andros")

        # First version → UAT, accept, PROD → graduates in place to v1.0.0.
        for env in ("uat",):
            client.post(
                f"/api/v1/customers/{customer.id}/deploy",
                json={"version_number": "v0.1.0", "environment": env},
            )
        client.post(f"/api/v1/customers/{customer.id}/accept", json={"version_number": "v0.1.0"})
        first_prod = client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "prod"},
        )
        assert first_prod.json()["bumped_to"] == "v1.0.0"

        # Second, DIFFERENT version → UAT, accept, PROD. The project already had a prod deploy,
        # so this one does NOT graduate: no bump, deploys as v0.2.0, its row is untouched.
        client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.2.0", "environment": "uat"},
        )
        client.post(f"/api/v1/customers/{customer.id}/accept", json={"version_number": "v0.2.0"})
        second_prod = client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.2.0", "environment": "prod"},
        )
        assert second_prod.status_code == 200, second_prod.text
        assert second_prod.json()["ok"] is True
        assert second_prod.json()["bumped_to"] is None  # no re-graduation on the 2nd prod deploy

        db_session.expire_all()
        by_id = {
            v.id: v for v in db_session.execute(select(Version).where(Version.project_id == project.id)).scalars().all()
        }
        # The graduated first version and the ungraduated second version coexist as distinct rows.
        assert by_id[first_id].version_number == "v1.0.0"
        assert by_id[first_id].status == "released"
        assert by_id[second_id].version_number == "v0.2.0"  # untouched — no rename, no second v1.0.0


# ---------------------------------------------------------------------------
# §3.6 graduation is gated on deploy SUCCESS — the KEY mutation (promote-in-place
# only on ``first_prod and ok``) exercised on the FAILURE + idempotent paths.
# ---------------------------------------------------------------------------


class TestGraduationGatedOnDeploySuccess:
    def test_failed_first_prod_deploy_does_not_graduate(self, client, db_session, prod_failing_deploy_runner):
        """⚠ A FAILED first-PROD deploy leaves the version un-graduated + resolvable for a retry (§3.6).

        The promote-in-place graduation is gated on ``first_prod and ok`` — a runner returning ``ok=False``
        records a ``failed`` event, drops the bump signal, and leaves the built version under its ORIGINAL
        number/status with NO v1.0.0 shell, so the Manažér can simply re-deploy it.
        """
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        version = _seed_verified_version(db_session, project, "v0.1.0")
        version_id = version.id
        customer = _seed_customer(db_session, project, "andros")

        # UAT deploy succeeds, then accept → the PROD gate (§3.5) is satisfied.
        uat = client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "uat"},
        )
        assert uat.status_code == 200, uat.text
        assert uat.json()["ok"] is True
        accept = client.post(f"/api/v1/customers/{customer.id}/accept", json={"version_number": "v0.1.0"})
        assert accept.status_code == 200, accept.text

        # PROD deploy — the runner FAILS (ok=False). The action itself returns 200; the DEPLOY failed.
        prod = client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "prod"},
        )
        assert prod.status_code == 200, prod.text
        result = prod.json()
        assert result["ok"] is False
        assert result["bumped_to"] is None  # a failed deploy drops the bump signal
        assert result["url"] is None

        # No graduation: the built version stays under v0.1.0 with its seed status, and NO v1.0.0 row was created.
        db_session.expire_all()
        rows = db_session.execute(select(Version).where(Version.project_id == project.id)).scalars().all()
        assert len(rows) == 1, "a failed deploy must not create a v1.0.0 shell"
        stayed = rows[0]
        assert stayed.id == version_id
        assert stayed.version_number == "v0.1.0"  # NOT graduated
        assert stayed.status == "planned"  # seed default, untouched — a failed deploy never marks released
        # Still resolvable under its original number for a retry (the deploy is repeatable).
        assert deploy_service._resolve_version(db_session, project.id, "v0.1.0").id == version_id
        # The failure is in the audit log as a 'failed' prod deploy event.
        prod_events = [
            e
            for e in deploy_service.list_events(db_session, customer.id)
            if e.environment == "prod" and e.event_type == "deploy"
        ]
        assert prod_events and prod_events[0].status == "failed"

    def test_deploying_already_v1_0_0_version_is_idempotent(self, client, db_session, fake_deploy_runner):
        """Deploying a version ALREADY numbered v1.0.0 neither errors nor double-graduates (§3.6 idempotent).

        A free-form ``v1.0.0`` (manually numbered) reaching its first PROD deploy hits the graduation's
        idempotent branch (``version_number == target`` → no rename, just mark released). A SECOND PROD deploy —
        now with prod history — does not re-graduate. Exactly ONE v1.0.0 row survives each pass; no raise.
        """
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        version = _seed_verified_version(db_session, project, "v1.0.0")  # already carries the graduation target
        version_id = version.id
        customer = _seed_customer(db_session, project, "andros")

        client.post(f"/api/v1/customers/{customer.id}/deploy", json={"version_number": "v1.0.0", "environment": "uat"})
        client.post(f"/api/v1/customers/{customer.id}/accept", json={"version_number": "v1.0.0"})

        first_prod = client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v1.0.0", "environment": "prod"},
        )
        assert first_prod.status_code == 200, first_prod.text
        assert first_prod.json()["ok"] is True
        # First PROD (no prior prod history) → the idempotent branch marks it released + still reports the bump.
        assert first_prod.json()["bumped_to"] == "v1.0.0"

        db_session.expire_all()
        rows = db_session.execute(select(Version).where(Version.project_id == project.id)).scalars().all()
        assert len(rows) == 1  # no duplicate v1.0.0 shell
        assert rows[0].id == version_id
        assert rows[0].version_number == "v1.0.0"
        assert rows[0].status == "released"

        # SECOND PROD deploy of the same v1.0.0 — prod history now exists → not first_prod → no re-graduation, no error.
        second_prod = client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v1.0.0", "environment": "prod"},
        )
        assert second_prod.status_code == 200, second_prod.text
        assert second_prod.json()["ok"] is True
        assert second_prod.json()["bumped_to"] is None  # already graduated — no bump on the 2nd prod deploy

        db_session.expire_all()
        rows2 = db_session.execute(select(Version).where(Version.project_id == project.id)).scalars().all()
        assert len(rows2) == 1  # STILL exactly one row — no double-graduate
        assert rows2[0].version_number == "v1.0.0"
        assert rows2[0].status == "released"


# ---------------------------------------------------------------------------
# Per-customer independence (§3.3) — two customers on different versions
# ---------------------------------------------------------------------------


class TestPerCustomerIndependence:
    def test_two_customers_run_different_versions(self, client, db_session, fake_deploy_runner):
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        _seed_verified_version(db_session, project, "v1.0.0")
        _seed_verified_version(db_session, project, "v1.1.0")
        andros = _seed_customer(db_session, project, "andros")
        icc = _seed_customer(db_session, project, "icc")

        # ANDROS UAT → v1.0.0; ICC UAT → v1.1.0 (different versions, same time).
        client.post(
            f"/api/v1/customers/{andros.id}/deploy",
            json={"version_number": "v1.0.0", "environment": "uat"},
        )
        client.post(
            f"/api/v1/customers/{icc.id}/deploy",
            json={"version_number": "v1.1.0", "environment": "uat"},
        )

        matrix = client.get(f"/api/v1/projects/{project.slug}/deploy-matrix").json()
        by_slug = {r["customer_slug"]: r for r in matrix["rows"]}
        assert by_slug["andros"]["uat_version"] == "v1.0.0"
        assert by_slug["icc"]["uat_version"] == "v1.1.0"  # genuinely different

    def test_redeploy_preserves_secrets_by_default(self, client, db_session, fake_deploy_runner):
        """⚠ SAFETY INVARIANT: a redeploy does NOT rotate secrets by default (§3.7)."""
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        _seed_verified_version(db_session, project, "v0.1.0")
        _seed_verified_version(db_session, project, "v0.2.0")
        customer = _seed_customer(db_session, project, "andros")

        client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.1.0", "environment": "uat"},
        )
        # A later version to an EXISTING instance — must preserve (force_fresh False).
        client.post(
            f"/api/v1/customers/{customer.id}/deploy",
            json={"version_number": "v0.2.0", "environment": "uat"},
        )
        # The runner was invoked with force_fresh=False on every call (preserve-by-default).
        assert fake_deploy_runner, "runner was never called"
        assert all(call["force_fresh"] is False for call in fake_deploy_runner)


# ---------------------------------------------------------------------------
# Service-level unit checks (the matrix helpers — no HTTP)
# ---------------------------------------------------------------------------


class TestMatrixServiceHelpers:
    def test_list_verified_versions_excludes_in_flight(self, db_session):
        creator = User(
            username=f"svc_{_uuid.uuid4().hex[:8]}",
            email=f"svc_{_uuid.uuid4().hex[:8]}@test.local",
            password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt(rounds=4)).decode(),
            role="ri",
            is_active=True,
        )
        db_session.add(creator)
        db_session.flush()
        project = _seed_project(db_session, creator=creator)
        _seed_verified_version(db_session, project, "v0.1.0")
        _seed_unverified_version(db_session, project, "v0.2.0")

        verified = deploy_service.list_verified_versions(db_session, project.id)
        assert verified == ["v0.1.0"]

    def test_instance_url_is_single_source_of_truth(self, db_session):
        creator = User(
            username=f"url_{_uuid.uuid4().hex[:8]}",
            email=f"url_{_uuid.uuid4().hex[:8]}@test.local",
            password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt(rounds=4)).decode(),
            role="ri",
            is_active=True,
        )
        db_session.add(creator)
        db_session.flush()
        project = _seed_project(db_session, creator=creator)
        customer = _seed_customer(db_session, project, "andros")
        # The matrix UAT URL must equal the runner-built URL for the same slug.
        base = (customer.subdomain or customer.slug).strip().lower()
        app = deploy_service.uat_provisioner.derive_uat_slug(project.slug)
        # Audit fix 2026-07-11: the per-customer UAT is per-PROJECT (uat-<customer>-<app>), never the old flat
        # uat-<customer>-uat. The matrix link matches the URL the runner builds from <customer>-<app>.
        assert deploy_service._instance_url(customer, "uat", project) == deploy_service._url_for_instance_slug(
            f"{base}-{app}"
        )
        assert deploy_service._instance_url(customer, "uat", project) == f"https://uat-{base}-{app}.isnex.eu"
        # The env-carrying slug (used only to detect prod + recover the customer) stays <customer>-<env>.
        assert deploy_service._instance_slug(customer, "uat") == f"{base}-uat"


# ---------------------------------------------------------------------------
# WHY Nasadiť is closed (v4.0.54) — the ``deployability`` block behind the button
# ---------------------------------------------------------------------------
#
# "Verified" is RECOMPUTED against live git on every matrix read, so a finished version silently drops out
# of ``verified_versions`` the moment the project's code moves past the commit it was checked at. Until
# v4.0.54 the matrix carried only that (now empty) list, so the UAT/PROD screen greyed Nasadiť out with NO
# cause, NO named version and NO way back — a Junior had to leave the cockpit for a terminal to recover
# (incident 2026-07-26, nex-websites). These pin the provenance → CAUSE mapping and the re-verify
# authorization the frontend cannot derive.
#
# ``version_verified`` / ``_repo_head`` are monkeypatched on the ``orchestrator`` MODULE:
# ``deploy.deployability`` imports both INSIDE its body, so the patched attributes are what it resolves at
# call time (the same trick as integration/test_reverify_drift_offered.py) — no throwaway git repo needed.
# The provenance computation itself is exercised end-to-end in tests/test_version_verified.py; these target
# the mapping, not the drift detection.
#
# The cause STRINGS are asserted as literals on purpose: they are the wire contract the deploy screen
# switches on (DeployBlockNotice.tsx), so a renamed constant must fail HERE, not silently in the browser.


@pytest.fixture()
def fake_repo_head(monkeypatch):
    """Pin the repo HEAD read to a constant — no ``git`` is spawned for a project dir that isn't on disk.

    ``deployability`` reads HEAD ONCE per project (batch) and hands it to every ``version_verified`` call,
    so the value only has to be stable, not real.
    """
    monkeypatch.setattr(orchestrator, "_repo_head", lambda *a, **k: "deadbeef")
    return "deadbeef"


def _force_provenance(monkeypatch, provenance: str) -> None:
    """Make every ``version_verified`` call report NOT-verified with the given provenance.

    ``deployability`` branches purely on the provenance string, so this drives each cause without building a
    repo whose HEAD has genuinely moved past a recorded PASS/Hotovo SHA.
    """
    monkeypatch.setattr(orchestrator, "version_verified", lambda *a, **k: (False, provenance))


def _seed_user(db, *, role: str, prefix: str) -> User:
    """A user of the given ICC role (``ri``/``ha``/``shu``) — the authz subject of the re-verify offer."""
    suffix = _uuid.uuid4().hex[:8]
    user = User(
        username=f"{prefix}_{suffix}",
        email=f"{prefix}_{suffix}@test.local",
        password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt(rounds=4)).decode(),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _seed_signed_off_version(db, project: Project, version_number: str) -> Version:
    """A version the Manažér SIGNED as Hotovo (conversation build) — the signature, NO Auditor verdict.

    The signature is the one ``stage='priprava'`` ∧ ``kind='notification'`` ∧ ``payload.hotovo`` message
    that ``orchestrator.apply_action('hotovo')`` records; ``orchestrator.ever_signed_off`` reads exactly
    this shape (staleness ignored). No PASS verdict is written, so the version was finished ONCE without
    ever being Auditor-verified — the input shape for the stale sign-off cause.
    """
    version = Version(project_id=project.id, version_number=version_number, name=version_number)
    db.add(version)
    db.flush()
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="done",
            current_actor="ai_agent",
            status="done",
            next_action="",
        )
    )
    db.flush()
    orchestrator._record_message(
        db,
        version_id=version.id,
        stage="priprava",
        author="manazer",
        recipient="ai_agent",
        kind="notification",
        content="Hotovo",
        payload={"phase": "priprava", "hotovo": True, "hotovo_sha": "cafebabe"},
    )
    db.flush()
    return version


class TestDeployabilityCause:
    """The matrix EXPLAINS a closed Nasadiť: cause + implicated version + who may recover it (v4.0.54)."""

    def test_drift_names_the_affected_version(self, db_session, monkeypatch, fake_repo_head):
        """⚠ THE INCIDENT (2026-07-26): a finished version whose code moved on leaves ``verified_versions``
        EMPTY — the matrix must then say WHY and NAME the version, never grey the button out in silence.

        The version is identified by id too: that id is what the re-verify action is posted against.
        """
        owner = _seed_user(db_session, role="ri", prefix="drift_owner")
        project = _seed_project(db_session, creator=owner)
        version = _seed_verified_version(db_session, project, "v0.1.0")
        _force_provenance(monkeypatch, "hotovo_drift")  # signed at a commit the repo has since moved past

        matrix = deploy_service.build_matrix(db_session, project)

        # Nothing deployable — the state that used to render as an unexplained grey button.
        assert matrix["verified_versions"] == []
        block = matrix["deployability"]
        assert block["cause"] == "drift"
        assert block["version_number"] == "v0.1.0"
        assert block["version_id"] == version.id
        # No user was supplied → the offer is never granted on a guess.
        assert block["can_reverify"] is False

    def test_drift_mid_reverify_is_distinguished_from_plain_drift(self, db_session, monkeypatch, fake_repo_head):
        """A drifted version whose re-verification is RUNNING reports its own cause.

        The version leaves the ``done`` stage for the whole run, so the deployable list looks identical
        before and after the click — without this cause the screen would keep offering "Over znova" for
        minutes and read as if nothing happened.
        """
        owner = _seed_user(db_session, role="ri", prefix="running_owner")
        project = _seed_project(db_session, creator=owner)
        version = _seed_verified_version(db_session, project, "v0.1.0")
        state = db_session.query(PipelineState).filter_by(version_id=version.id).one()
        # The shape apply_action('overit_znovu') leaves for a sha_drift: re-entered Verifikácia as a RE-GATE,
        # turn in flight. The re-gate flag is the evidence — a merely busy version is NOT re-verifying.
        state.status = "agent_working"
        state.current_stage = "verifikacia"
        state.is_regate = True
        db_session.flush()
        _force_provenance(monkeypatch, "sha_drift")

        block = deploy_service.deployability(db_session, project, verified_versions=[], user=owner)

        assert block["cause"] == "reverify_running"
        assert block["version_number"] == "v0.1.0"
        # A run is already in flight — no second trigger, even for a user who would otherwise qualify.
        assert block["can_reverify"] is False

    def test_stale_signoff_offers_no_reverify_button(self, db_session, monkeypatch, fake_repo_head):
        """⚠ REGRESSION GUARD: a sign-off outranked by later work is NOT re-verifiable.

        ``overit_znovu``'s handler rejects this shape, so offering the button would only move the dead end
        one click further (a 400 instead of a grey button). The project OWNER — an ``ri``, i.e. the most
        privileged subject there is — is passed in deliberately: ``can_reverify`` must be False because of
        the CAUSE, not because nobody was authorized.
        """
        owner = _seed_user(db_session, role="ri", prefix="stale_owner")
        project = _seed_project(db_session, creator=owner)
        version = _seed_signed_off_version(db_session, project, "v0.1.0")
        # The Hotovo signature IS on record (staleness ignored) — this is what separates a stale sign-off
        # from a version that never got anywhere.
        assert orchestrator.ever_signed_off(db_session, version.id) is True
        _force_provenance(monkeypatch, "no_pass")  # a fresher build outranked the signature

        block = deploy_service.deployability(db_session, project, verified_versions=[], user=owner)

        assert block["cause"] == "stale_signoff"
        assert block["version_number"] == "v0.1.0"
        assert block["version_id"] == version.id
        assert block["can_reverify"] is False

    def test_never_finished_project_implicates_no_version(self, db_session, fake_repo_head):
        """A project whose only version never got anywhere has nothing to explain and nothing to re-verify —
        so no version is named (the screen must not blame an innocent in-flight version)."""
        owner = _seed_user(db_session, role="ri", prefix="fresh_owner")
        project = _seed_project(db_session, creator=owner)
        _seed_unverified_version(db_session, project, "v0.1.0")  # still in Programovanie, never signed off

        block = deploy_service.deployability(db_session, project, verified_versions=[], user=owner)

        assert block["cause"] == "none_finished"
        assert block["version_number"] is None
        assert block["version_id"] is None
        assert block["can_reverify"] is False

    def test_deployable_project_stays_silent(self, db_session, fake_repo_head):
        """The happy path renders NOTHING: a deployable version → cause ``ok``, no version implicated, no
        button — the notice is absent and the normal screen is untouched."""
        owner = _seed_user(db_session, role="ri", prefix="ok_owner")
        project = _seed_project(db_session, creator=owner)
        _seed_verified_version(db_session, project, "v0.1.0")

        matrix = deploy_service.build_matrix(db_session, project, owner)

        assert matrix["verified_versions"] == ["v0.1.0"]
        block = matrix["deployability"]
        assert block["cause"] == "ok"
        assert block["version_number"] is None
        assert block["version_id"] is None
        # Never offered while a deploy is actually possible — there is nothing to recover.
        assert block["can_reverify"] is False

    def test_reports_the_semantically_newest_drifted_version(self, db_session, monkeypatch, fake_repo_head):
        """The candidate scan is NEWEST-FIRST by SEMVER — the explanation must be about the version the
        manager would actually deploy.

        ``v0.9.0`` is seeded FIRST and sorts ABOVE ``v0.10.0`` as a plain string, so both an insertion-order
        scan and a string-order scan would name the wrong (older) version.
        """
        owner = _seed_user(db_session, role="ri", prefix="newest_owner")
        project = _seed_project(db_session, creator=owner)
        _seed_verified_version(db_session, project, "v0.9.0")
        newest = _seed_verified_version(db_session, project, "v0.10.0")
        _force_provenance(monkeypatch, "sha_drift")

        block = deploy_service.deployability(db_session, project, verified_versions=[], user=owner)

        assert block["cause"] == "drift"
        assert block["version_number"] == "v0.10.0"
        assert block["version_id"] == newest.id

    def test_reverify_offer_follows_ownership(self, db_session, monkeypatch, fake_repo_head):
        """``can_reverify`` mirrors the action's own gate — the frontend cannot derive it (the payload
        carries no project owner).

        ``overit_znovu`` goes through ``authz.assert_version_access(...)``, which under the ownership
        model asks one question: is it his project (or is he ``admin``)? The role is irrelevant, and the
        test previously asserted the opposite — that ``ri`` drives every project and ``ha`` does not.
        """
        owner = _seed_user(db_session, role="shu", prefix="junior_owner")
        foreign_ri = _seed_user(db_session, role="ri", prefix="foreign_ri")
        foreign_ha = _seed_user(db_session, role="ha", prefix="foreign_ha")
        admin = _seed_user(db_session, role="shu", prefix="admin_acct")
        admin.username = "admin"  # the ACCOUNT; note its role here is shu and it does not matter
        db_session.flush()
        project = _seed_project(db_session, creator=owner)
        _seed_verified_version(db_session, project, "v0.1.0")
        _force_provenance(monkeypatch, "hotovo_drift")

        def _can_reverify(user) -> bool:
            block = deploy_service.deployability(db_session, project, verified_versions=[], user=user)
            assert block["cause"] == "drift"  # the offer is only ever evaluated on the recoverable cause
            return block["can_reverify"]

        assert _can_reverify(owner) is True  # his project
        assert _can_reverify(admin) is True  # the admin account reaches everything
        assert _can_reverify(foreign_ri) is False  # the ri ROLE grants nothing here any more
        assert _can_reverify(foreign_ha) is False

    def test_awaiting_signoff_is_never_called_stale(self, db_session, monkeypatch, fake_repo_head):
        """A version that PASSED but has not been approved yet is one click away — not "stale".

        This is the ORDINARY post-PASS state: ``_settle_verifikacia`` leaves ``status='awaiting_manazer'``
        with the stage still at Verifikácia, so ``list_verified_versions`` (which requires the ``done``
        stage) excludes it while ``version_verified`` reports it VERIFIED. Reading only the provenance and
        ignoring the verified flag made the screen announce "later work outranked the sign-off" — false on
        both counts, on the single most common not-deployable state.
        """
        owner = _seed_user(db_session, role="ri", prefix="await_owner")
        project = _seed_project(db_session, creator=owner)
        version = _seed_verified_version(db_session, project, "v0.1.0")
        state = db_session.query(PipelineState).filter_by(version_id=version.id).one()
        state.current_stage = "verifikacia"  # PASS recorded, waiting for the Hotovo approval
        state.status = "awaiting_manazer"
        db_session.flush()
        monkeypatch.setattr(orchestrator, "version_verified", lambda *a, **k: (True, "sha_match"))

        block = deploy_service.deployability(db_session, project, verified_versions=[], user=owner)

        assert block["cause"] == "awaiting_signoff"
        assert block["version_number"] == "v0.1.0"
        # Nothing to re-verify — the check already passed; the remedy is an approval, not a re-run.
        assert block["can_reverify"] is False

    def test_matrix_endpoint_carries_the_cause_and_evaluates_the_caller(
        self, client, db_session, monkeypatch, fake_repo_head
    ):
        """The HTTP read must carry ``deployability`` AND evaluate ``can_reverify`` for the AUTHENTICATED
        user — the one line that connects the service to the screen.

        Without this, dropping the user argument in the route would keep every service test green while
        ``can_reverify`` silently became False in production, putting the manager straight back at the
        terminal-only dead end this whole change exists to remove.
        """
        user = _current_user(db_session)
        project = _seed_project(db_session, creator=user)
        _seed_verified_version(db_session, project, "v0.1.0")
        _seed_customer(db_session, project, "andros")
        _force_provenance(monkeypatch, "hotovo_drift")

        resp = client.get(f"/api/v1/projects/{project.slug}/deploy-matrix")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["verified_versions"] == []
        assert body["deployability"]["cause"] == "drift"
        assert body["deployability"]["version_number"] == "v0.1.0"
        assert body["deployability"]["version_id"] is not None
        assert body["deployability"]["can_reverify"] is True

    def test_can_accept_follows_ownership(self, db_session, monkeypatch, fake_repo_head):
        """Acceptance OPENS PROD (§3.5), so it stays ri-only (v4.0.35/D3) even for a project's owner.

        Deliberately NARROWER than the UAT deploy, which IS owner-or-ri. The flag exists so the button can
        be disabled WITH a reason: an owner-Junior saw "Akceptovať" enabled on their own project and got a
        403 on click. Widening the gate here would quietly widen the PROD gate — the opposite of D3.
        """
        owner = _seed_user(db_session, role="shu", prefix="accept_owner")
        manager = _seed_user(db_session, role="ri", prefix="accept_ri")
        medior = _seed_user(db_session, role="ha", prefix="accept_ha")
        project = _seed_project(db_session, creator=owner)
        _seed_verified_version(db_session, project, "v0.1.0")
        _seed_customer(db_session, project, "andros")

        admin = _seed_user(db_session, role="shu", prefix="accept_admin")
        admin.username = "admin"
        db_session.flush()

        def _can_accept(user) -> bool:
            return deploy_service.build_matrix(db_session, project, user)["can_accept"]

        # Ownership decides, here as everywhere. The owner may accept his OWN project's UAT — the
        # separate "acceptance is the PROD gate, so it needs role ri" clause went with the tier model.
        assert _can_accept(owner) is True
        assert _can_accept(admin) is True
        assert _can_accept(manager) is False  # the ri ROLE, on a project he does not own
        assert _can_accept(medior) is False
        # An unauthenticated/fixture-built matrix must never imply permission.
        assert deploy_service.build_matrix(db_session, project)["can_accept"] is False

    def test_can_deploy_prod_follows_ownership(self, db_session, fake_repo_head):
        """The PROD 'Nasadiť' carries the same ri-only gate as acceptance — and needs its own flag.

        v4.0.55 gave 'Akceptovať' this treatment and stopped there, so the PROD tab's deploy button went on
        looking live to a Junior owner / a Medior and 403-ing on click. It cannot be hidden by page-level
        role either: the SAME button on the UAT tab is legitimately open to the project owner (D3).
        """
        owner = _seed_user(db_session, role="shu", prefix="depl_owner")
        manager = _seed_user(db_session, role="ri", prefix="depl_ri")
        medior = _seed_user(db_session, role="ha", prefix="depl_ha")
        project = _seed_project(db_session, creator=owner)
        _seed_verified_version(db_session, project, "v0.1.0")
        _seed_customer(db_session, project, "andros")

        admin = _seed_user(db_session, role="shu", prefix="depl_admin")
        admin.username = "admin"
        db_session.flush()

        def _can_deploy_prod(user) -> bool:
            return deploy_service.build_matrix(db_session, project, user)["can_deploy_prod"]

        # The owner deploys his own project to BOTH environments. The old "PROD stays behind the ri
        # role" clause was the last survivor of the tier model and went with it.
        assert _can_deploy_prod(owner) is True
        assert _can_deploy_prod(admin) is True
        assert _can_deploy_prod(manager) is False
        assert _can_deploy_prod(medior) is False
        assert deploy_service.build_matrix(db_session, project)["can_deploy_prod"] is False

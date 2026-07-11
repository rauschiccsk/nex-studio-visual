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

    async def _runner(*, project_slug, uat_slug, version_number, force_fresh):
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

    async def _runner(*, project_slug, uat_slug, version_number, force_fresh):
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

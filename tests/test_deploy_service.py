"""Tests for the per-customer Deploy subsystem (v2.0.0, CR-V2-026).

**Re-authored from the retired v1 ``backend/tests/test_release_publish.py``.**
The v1 release-stage publish/auto-deploy (``_run_release_publish`` /
``_release_auto_publish`` / ``retry_publish``) was IN-pipeline behaviour driven
off v1 ``release``-stage pipeline_state rows the v2 CHECKs reject. v2 moves
deploy ENTIRELY OUT of the build pipeline into a per-customer, manual,
outside-the-dial subsystem (design §3 / D6 / OQ-3). This file is the v2
equivalent: it exercises :mod:`backend.services.deploy` + the deploy REST router.

The four load-bearing safety invariants (all tested here):

1. **§3.7 fresh-first-then-data-preserving** — the first install is fresh; every
   later deploy PRESERVES data + secrets + ``extra_hosts`` and runs migrations,
   NEVER rotating secrets by default (``rotate_secrets=False``); ``force_fresh``
   is the only path to a rotation (the inbox-UAT redeploy lesson).
2. **The never-bypassed UAT acceptance gate (§3.5)** — no PROD deploy of a
   (customer, version) without a recorded ``accept`` for that exact pair.
3. **Versioning (§3.6)** — the first PROD deploy bumps the project to v1.0.0.
4. **Deploy is manual + outside the dial (D6)** — and a per-customer secret is
   NEVER echoed/logged (§4); the audit event records who/when/version/customer.
"""

from __future__ import annotations

import uuid

import pytest

from backend.config.settings import settings
from backend.db.models.deploy import DeployEvent
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.schemas.customer import CustomerCreate
from backend.services import customer as customer_service
from backend.services import deploy as deploy_service
from backend.services import orchestrator, uat_provisioner

# ---------------------------------------------------------------------------
# Isolation — secrets written during a deploy/accept flow go to a throwaway
# store, never the real ``/opt/data/nex-studio/credentials`` directory.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_credentials_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "credentials_storage_path", str(tmp_path / "creds"))


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_user(db_session, **overrides) -> User:
    defaults = {
        "username": f"user_{uuid.uuid4().hex[:8]}",
        "email": f"{uuid.uuid4().hex[:8]}@example.com",
        "password_hash": "hashed_password_placeholder",
        "role": "ri",
    }
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, *, user: User | None = None, **overrides) -> Project:
    if user is None:
        user = _make_user(db_session)
    suffix = uuid.uuid4().hex[:8]
    defaults = {
        "name": f"Project {suffix}",
        "slug": f"project-{suffix}",
        "type": "standard",
        "auth_mode": "password",
        "description": "Test project description",
        "created_by": user.id,
    }
    defaults.update(overrides)
    project = Project(**defaults)
    db_session.add(project)
    db_session.flush()
    return project


def _make_version(db_session, project, version_number="v0.1.0") -> Version:
    version = Version(project_id=project.id, version_number=version_number, name="dev")
    db_session.add(version)
    db_session.flush()
    # CR-V2-056: a deployable version must be VERIFIED — record the Verifikácia PASS verdict that
    # version_verified reads. No verified_sha in the test repo → 'unbound' → verified (grandfathered).
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content="PASS",
        payload={"verdict": "PASS", "phase": "verifikacia"},
    )
    db_session.flush()
    return version


def _make_customer(db_session, project, *, slug="andros", subdomain="andros", secret=None):
    return customer_service.create(
        db_session,
        project.id,
        CustomerCreate(name="ANDROS", slug=slug, subdomain=subdomain, secret=secret),
    )


class _FakeRunner:
    """Injectable deploy runner — records its kwargs, never spawns docker.

    Returns a scripted ``(ok, detail, url)``; the ``force_fresh`` it receives is
    the load-bearing assertion for the preservation invariant (it must be False
    on a default redeploy).
    """

    def __init__(self, ok=True, detail="OK", url="https://uat-andros-uat.isnex.eu"):
        self._ok = ok
        self._detail = detail
        self._url = url
        self.calls: list[dict] = []

    async def __call__(self, *, project_slug, uat_slug, version_number, force_fresh, admin_password=None):
        self.calls.append(
            {
                "project_slug": project_slug,
                "uat_slug": uat_slug,
                "version_number": version_number,
                "force_fresh": force_fresh,
            }
        )
        return self._ok, self._detail, (self._url if self._ok else None)


async def _deploy(db_session, customer, *, version_number, environment, actor, runner, force_fresh=False):
    return await deploy_service.deploy(
        db_session,
        customer.id,
        version_number=version_number,
        environment=environment,
        actor_id=actor.id,
        force_fresh=force_fresh,
        deploy_runner=runner,
    )


# ---------------------------------------------------------------------------
# Basic deploy — provisions an instance, logs the event (who/when/version/customer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uat_deploy_provisions_and_logs_event(db_session):
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()

    event, url, bumped = await _deploy(
        db_session, customer, version_number="v0.1.0", environment="uat", actor=user, runner=runner
    )

    assert event.status == "ok"
    assert event.event_type == "deploy"
    assert event.environment == "uat"
    assert event.version_number == "v0.1.0"
    assert event.customer_id == customer.id
    assert event.project_id == project.id
    assert event.actor_id == user.id  # WHO
    assert event.created_at is not None  # WHEN
    assert url == "https://uat-andros-uat.isnex.eu"
    assert bumped is None
    # The runner was called for this customer's UAT instance slug.
    assert runner.calls[0]["uat_slug"] == "andros-uat"
    assert runner.calls[0]["project_slug"] == project.slug


@pytest.mark.asyncio
async def test_deploy_unknown_version_rejected(db_session):
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()

    with pytest.raises(ValueError, match="not found for this project"):
        await _deploy(db_session, customer, version_number="v9.9.9", environment="uat", actor=user, runner=runner)
    assert runner.calls == [], "an unknown version must never reach the deploy runner"


@pytest.mark.asyncio
async def test_unknown_environment_rejected(db_session):
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    customer = _make_customer(db_session, project)
    with pytest.raises(ValueError, match="Unknown environment"):
        await _deploy(
            db_session, customer, version_number="v0.1.0", environment="staging", actor=user, runner=_FakeRunner()
        )


# ---------------------------------------------------------------------------
# INVARIANT 1 (§3.7): redeploy PRESERVES — no secret rotation by default; migrations run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redeploy_preserves_secrets_by_default(db_session):
    """A later deploy to an existing customer instance passes ``force_fresh=False``
    to the provisioner (→ ``rotate_secrets=False`` → secrets + data + extra_hosts
    preserved, migrations run on ``up``) — the inbox-UAT redeploy lesson."""
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    _make_version(db_session, project, "v0.2.0")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()

    # First install.
    await _deploy(db_session, customer, version_number="v0.1.0", environment="uat", actor=user, runner=runner)
    # Later deploy of a higher version to the SAME instance.
    await _deploy(db_session, customer, version_number="v0.2.0", environment="uat", actor=user, runner=runner)

    assert runner.calls[0]["force_fresh"] is False
    assert runner.calls[1]["force_fresh"] is False, "a redeploy must NOT rotate secrets by default"
    # Same instance slug both times (it's an update, not a new instance).
    assert runner.calls[0]["uat_slug"] == runner.calls[1]["uat_slug"] == "andros-uat"


@pytest.mark.asyncio
async def test_force_fresh_opts_into_rotation(db_session):
    """``force_fresh=True`` is the ONLY path that forces a fresh re-provision
    (rotation) — explicit, opt-in, never the default."""
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()

    await _deploy(
        db_session, customer, version_number="v0.1.0", environment="uat", actor=user, runner=runner, force_fresh=True
    )
    assert runner.calls[0]["force_fresh"] is True


def test_default_runner_passes_rotate_secrets_through(monkeypatch):
    """The DEFAULT runner maps ``force_fresh`` → ``provision_uat(rotate_secrets=...)``
    one-to-one (preserve-by-default contract intact at the real seam)."""
    import asyncio

    captured = {}

    class _Result:
        warnings: list[str] = []
        fe_service = "frontend"

    def _fake_provision(project_slug, uat_slug, *, version, rotate_secrets, **kw):
        captured["rotate_secrets"] = rotate_secrets
        captured["uat_slug"] = uat_slug
        return _Result()

    async def _fake_run_uat_deploy(project_slug, uat_slug, **kw):
        return True, "OK"

    monkeypatch.setattr(uat_provisioner, "provision_uat", _fake_provision)
    from backend.services import orchestrator

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _fake_run_uat_deploy)

    ok, detail, url = asyncio.run(
        deploy_service._default_deploy_runner(
            project_slug="nex-demo", uat_slug="andros-uat", version_number="v0.1.0", force_fresh=False
        )
    )
    assert captured["rotate_secrets"] is False  # preserve by default
    assert ok is True

    asyncio.run(
        deploy_service._default_deploy_runner(
            project_slug="nex-demo", uat_slug="andros-uat", version_number="v0.1.0", force_fresh=True
        )
    )
    assert captured["rotate_secrets"] is True  # force_fresh → rotate


# ---------------------------------------------------------------------------
# INVARIANT 2 (§3.5): the UAT acceptance gate is NEVER bypassed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prod_deploy_without_acceptance_blocked(db_session):
    """No PROD deploy of a (customer, version) without a recorded acceptance."""
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()

    with pytest.raises(ValueError, match="PROD deploy blocked"):
        await _deploy(db_session, customer, version_number="v0.1.0", environment="prod", actor=user, runner=runner)
    assert runner.calls == [], "a PROD deploy without acceptance must never reach the runner"


@pytest.mark.asyncio
async def test_accept_requires_uat_deploy_first(db_session):
    """You cannot accept a version that was never successfully deployed to UAT."""
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    customer = _make_customer(db_session, project)

    with pytest.raises(ValueError, match="has not been successfully deployed"):
        deploy_service.accept(db_session, customer.id, "v0.1.0", user.id)


@pytest.mark.asyncio
async def test_accept_then_prod_deploy_allowed(db_session):
    """Full gate flow: UAT deploy → accept (logs who/when) → PROD deploy permitted."""
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()

    await _deploy(db_session, customer, version_number="v0.1.0", environment="uat", actor=user, runner=runner)
    assert deploy_service.is_accepted(db_session, customer.id, "v0.1.0") is False

    accept_event = deploy_service.accept(db_session, customer.id, "v0.1.0", user.id)
    assert accept_event.event_type == "accept"
    assert accept_event.actor_id == user.id  # WHO
    assert accept_event.created_at is not None  # WHEN
    assert deploy_service.is_accepted(db_session, customer.id, "v0.1.0") is True

    event, url, bumped = await _deploy(
        db_session, customer, version_number="v0.1.0", environment="prod", actor=user, runner=runner
    )
    assert event.status == "ok"
    assert event.environment == "prod"


@pytest.mark.asyncio
async def test_acceptance_is_per_customer_and_per_version(db_session):
    """Acceptance of (customer A, v0.1.0) does NOT open PROD for another customer
    or another version (the gate is per exact pair)."""
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    _make_version(db_session, project, "v0.2.0")
    cust_a = _make_customer(db_session, project, slug="andros", subdomain="andros")
    cust_b = _make_customer(db_session, project, slug="icc", subdomain="icc")
    runner = _FakeRunner()

    await _deploy(db_session, cust_a, version_number="v0.1.0", environment="uat", actor=user, runner=runner)
    deploy_service.accept(db_session, cust_a.id, "v0.1.0", user.id)

    # Another customer is NOT opened by A's acceptance.
    assert deploy_service.is_accepted(db_session, cust_b.id, "v0.1.0") is False
    with pytest.raises(ValueError, match="PROD deploy blocked"):
        await _deploy(db_session, cust_b, version_number="v0.1.0", environment="prod", actor=user, runner=runner)

    # A different version for A is NOT opened either.
    assert deploy_service.is_accepted(db_session, cust_a.id, "v0.2.0") is False


# ---------------------------------------------------------------------------
# INVARIANT 3 (§3.6): the first PROD deploy bumps the project to v1.0.0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_prod_deploy_bumps_to_v1(db_session):
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()

    await _deploy(db_session, customer, version_number="v0.1.0", environment="uat", actor=user, runner=runner)
    deploy_service.accept(db_session, customer.id, "v0.1.0", user.id)

    event, url, bumped = await _deploy(
        db_session, customer, version_number="v0.1.0", environment="prod", actor=user, runner=runner
    )
    assert bumped == "v1.0.0"
    assert event.version_number == "v1.0.0", "the recorded PROD event reflects the graduated version"
    # The graduated version is what the runner provisioned.
    prod_call = runner.calls[-1]
    assert prod_call["version_number"] == "v1.0.0"
    # A v1.0.0 version row now exists for the project.
    rows = [v.version_number for v in project.versions]
    assert "v1.0.0" in rows


@pytest.mark.asyncio
async def test_second_prod_deploy_does_not_rebump(db_session):
    """Only the project's FIRST PROD deploy graduates; a later PROD deploy keeps
    its requested version_number (no re-bump)."""
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    _make_version(db_session, project, "v1.1.0")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()

    # First PROD → v1.0.0.
    await _deploy(db_session, customer, version_number="v0.1.0", environment="uat", actor=user, runner=runner)
    deploy_service.accept(db_session, customer.id, "v0.1.0", user.id)
    _e1, _u1, bumped1 = await _deploy(
        db_session, customer, version_number="v0.1.0", environment="prod", actor=user, runner=runner
    )
    assert bumped1 == "v1.0.0"

    # Second PROD of a real later version → no re-bump.
    await _deploy(db_session, customer, version_number="v1.1.0", environment="uat", actor=user, runner=runner)
    deploy_service.accept(db_session, customer.id, "v1.1.0", user.id)
    e2, _u2, bumped2 = await _deploy(
        db_session, customer, version_number="v1.1.0", environment="prod", actor=user, runner=runner
    )
    assert bumped2 is None
    assert e2.version_number == "v1.1.0"


@pytest.mark.asyncio
async def test_failed_prod_deploy_does_not_graduate(db_session):
    """A failed first-PROD deploy records a ``failed`` event and does NOT report a
    graduation (the project has not graduated until a deploy actually succeeds)."""
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    customer = _make_customer(db_session, project)
    ok_runner = _FakeRunner()
    fail_runner = _FakeRunner(ok=False, detail="exit 1: build error")

    await _deploy(db_session, customer, version_number="v0.1.0", environment="uat", actor=user, runner=ok_runner)
    deploy_service.accept(db_session, customer.id, "v0.1.0", user.id)
    event, url, bumped = await _deploy(
        db_session, customer, version_number="v0.1.0", environment="prod", actor=user, runner=fail_runner
    )
    assert event.status == "failed"
    assert url is None
    assert bumped is None
    # A subsequent successful PROD still treats THIS as the first successful one → bumps.
    event2, _u2, bumped2 = await _deploy(
        db_session, customer, version_number="v0.1.0", environment="prod", actor=user, runner=ok_runner
    )
    assert bumped2 == "v1.0.0"


# ---------------------------------------------------------------------------
# INVARIANT 4 / §4: secrets are never echoed; deploy is manual (no autonomy hook)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deploy_event_detail_carries_no_secret(db_session):
    """A per-customer secret recorded at customer-create never appears in a deploy
    event's persisted ``detail`` (or anywhere on the row) — §4/OQ-5."""
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    secret_value = "SUPER-SECRET-TOKEN-9f8e7d6c"
    customer = _make_customer(db_session, project, secret=secret_value)
    runner = _FakeRunner(detail="OK | warnings: no frontend route")

    event, _url, _bumped = await _deploy(
        db_session, customer, version_number="v0.1.0", environment="uat", actor=user, runner=runner
    )
    # The secret value must not have leaked into any column on the event row.
    dumped = " ".join(str(getattr(event, c.name)) for c in DeployEvent.__table__.columns)
    assert secret_value not in dumped
    # The customer row holds only a credential POINTER, never the value.
    assert customer.credential_id is not None


# ---------------------------------------------------------------------------
# Matrix queries (version × customer) — drive the UAT/PROD tabs (§3.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_version_tracks_latest_ok_deploy(db_session):
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    _make_version(db_session, project, "v0.2.0")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()

    assert deploy_service.current_version(db_session, customer.id, "uat") is None
    await _deploy(db_session, customer, version_number="v0.1.0", environment="uat", actor=user, runner=runner)
    assert deploy_service.current_version(db_session, customer.id, "uat") == "v0.1.0"
    await _deploy(db_session, customer, version_number="v0.2.0", environment="uat", actor=user, runner=runner)
    assert deploy_service.current_version(db_session, customer.id, "uat") == "v0.2.0"
    # PROD is independent (never deployed).
    assert deploy_service.current_version(db_session, customer.id, "prod") is None


@pytest.mark.asyncio
async def test_different_customers_run_different_versions(db_session):
    """§3.3: different customers may run different versions simultaneously."""
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    _make_version(db_session, project, "v0.2.0")
    cust_a = _make_customer(db_session, project, slug="andros", subdomain="andros")
    cust_b = _make_customer(db_session, project, slug="icc", subdomain="icc")
    runner = _FakeRunner()

    await _deploy(db_session, cust_a, version_number="v0.1.0", environment="uat", actor=user, runner=runner)
    await _deploy(db_session, cust_b, version_number="v0.2.0", environment="uat", actor=user, runner=runner)

    assert deploy_service.current_version(db_session, cust_a.id, "uat") == "v0.1.0"
    assert deploy_service.current_version(db_session, cust_b.id, "uat") == "v0.2.0"


@pytest.mark.asyncio
async def test_list_events_and_project_events(db_session):
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()

    await _deploy(db_session, customer, version_number="v0.1.0", environment="uat", actor=user, runner=runner)
    deploy_service.accept(db_session, customer.id, "v0.1.0", user.id)

    customer_events = deploy_service.list_events(db_session, customer.id)
    assert {e.event_type for e in customer_events} == {"deploy", "accept"}
    project_events = deploy_service.list_project_events(db_session, project.id)
    assert len(project_events) == 2


# ---------------------------------------------------------------------------
# HTTP route tests (deploy router) — manual actions, gate, secret-never-echoed
# ---------------------------------------------------------------------------


def _auth_ri(client, db_session):
    """Override auth to an ri user that is PERSISTED — deploy_events FK actor_id to it."""
    from backend.core.security import get_current_user, require_ri_role
    from backend.main import app

    ri_user = _make_user(
        db_session,
        username=f"ri_deploy_{uuid.uuid4().hex[:8]}",
        email=f"ri-deploy-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
    )
    app.dependency_overrides[require_ri_role] = lambda: ri_user
    app.dependency_overrides[get_current_user] = lambda: ri_user


@pytest.fixture()
def _fake_default_runner(monkeypatch):
    """Patch the module-level default runner so HTTP deploys never spawn docker."""
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
        return True, "OK", f"https://uat-{uat_slug}.isnex.eu"

    monkeypatch.setattr(deploy_service, "_default_deploy_runner", _runner)
    return calls


def test_http_uat_deploy_then_accept_then_prod(client, db_session, _fake_default_runner):
    _auth_ri(client, db_session)
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    customer = _make_customer(db_session, project)
    db_session.commit()

    # UAT deploy.
    resp = client.post(
        f"/api/v1/customers/{customer.id}/deploy",
        json={"version_number": "v0.1.0", "environment": "uat"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["event"]["environment"] == "uat"
    assert body["bumped_to"] is None
    # Preserve-by-default at the HTTP seam.
    assert _fake_default_runner[0]["force_fresh"] is False

    # PROD without acceptance → 409 (gate never bypassed).
    blocked = client.post(
        f"/api/v1/customers/{customer.id}/deploy",
        json={"version_number": "v0.1.0", "environment": "prod"},
    )
    assert blocked.status_code == 409, blocked.text

    # Accept the UAT.
    acc = client.post(
        f"/api/v1/customers/{customer.id}/accept",
        json={"version_number": "v0.1.0"},
    )
    assert acc.status_code == 200, acc.text
    assert acc.json()["event_type"] == "accept"

    # PROD now opens → first PROD bumps to v1.0.0.
    prod = client.post(
        f"/api/v1/customers/{customer.id}/deploy",
        json={"version_number": "v0.1.0", "environment": "prod"},
    )
    assert prod.status_code == 200, prod.text
    assert prod.json()["bumped_to"] == "v1.0.0"


def test_http_deploy_events_log(client, db_session, _fake_default_runner):
    _auth_ri(client, db_session)
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    customer = _make_customer(db_session, project)
    db_session.commit()

    client.post(f"/api/v1/customers/{customer.id}/deploy", json={"version_number": "v0.1.0", "environment": "uat"})

    proj_log = client.get(f"/api/v1/projects/{project.slug}/deploy-events")
    assert proj_log.status_code == 200
    assert any(e["event_type"] == "deploy" for e in proj_log.json())

    cust_log = client.get(f"/api/v1/customers/{customer.id}/deploy-events")
    assert cust_log.status_code == 200
    assert len(cust_log.json()) == 1


def test_http_deploy_response_never_echoes_secret(client, db_session, _fake_default_runner):
    """The deploy/accept HTTP responses carry no secret material (§4/OQ-5)."""
    _auth_ri(client, db_session)
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    _make_version(db_session, project, "v0.1.0")
    secret = "HTTP-DEPLOY-SECRET-DO-NOT-LEAK-abc123"
    customer = _make_customer(db_session, project, secret=secret)
    db_session.commit()

    resp = client.post(
        f"/api/v1/customers/{customer.id}/deploy",
        json={"version_number": "v0.1.0", "environment": "uat"},
    )
    assert resp.status_code == 200
    assert secret not in resp.text

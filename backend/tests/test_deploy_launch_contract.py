"""The token-launch deploy contract — never ship an instance nobody can get into (v4.0.58).

An app opened by a one-click launch ticket (``auth_mode='token'``) needs three env vars wired under the
EXACT names ``uat_provisioner`` keys off. nex-websites shipped the same concept under its own name, so the
provisioner did not recognise it, synthesised a random key nobody else knows, and the deploy reported
SUCCESS — the breakage surfaced hours later when a Junior clicked "Spustiť" and had to be unblocked from a
terminal (incident 2026-07-26).

Two layers, deliberately different in kind:

* :func:`deploy.missing_launch_env_keys` — an UP-FRONT declaration check (before any teardown), reading the
  SAME merged source the provisioner renders from: ``.env.example`` PLUS the backend service's
  ``environment:`` block. Reading only ``.env.example`` would have blocked BOTH live token-launch apps,
  which declare these in compose — a guard that breaks working deploys to prevent a broken one is worse
  than the bug. It therefore also fails OPEN when the project is unreadable.
* :func:`deploy.uat_launch_wired` — the POST-deploy behavioural proof: mint a ticket exactly as the
  "Spustiť" button does. Catches what a declaration check cannot (correct names, key never wired because no
  paired NEX Manager → written EMPTY), and catches it on evidence rather than inference.
"""

from __future__ import annotations

import uuid as _uuid

import pytest
import yaml

from backend.db.models.customers import Customer
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import deploy as deploy_service
from backend.services import orchestrator, uat_provisioner


def _write_project(tmp_path, slug: str, *, env_example: dict, compose_backend_env: dict):
    project = tmp_path / slug
    project.mkdir(parents=True)
    (project / ".env.example").write_text(
        "\n".join(f"{k}={v}" for k, v in env_example.items()) + "\n", encoding="utf-8"
    )
    compose = {
        "services": {
            "backend": {"image": "x", "environment": compose_backend_env},
            "db": {"image": "postgres:16-alpine"},
        }
    }
    (project / "docker-compose.yml").write_text(yaml.safe_dump(compose), encoding="utf-8")
    return project


@pytest.fixture()
def projects_root(tmp_path, monkeypatch):
    from backend.services import claude_agent

    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    return tmp_path


@pytest.fixture()
def deploy_fixture(db_session):
    """Seed a customer + project + one deployable version, with the project's ``auth_mode`` under test."""

    def _seed(*, auth_mode: str):
        suffix = _uuid.uuid4().hex[:8]
        creator = User(
            username=f"lc_{suffix}",
            email=f"lc_{suffix}@test.local",
            password_hash="x",
            role="ri",
        )
        db_session.add(creator)
        db_session.flush()
        project = Project(
            name=f"Launch contract {suffix}",
            slug=f"launch-contract-{suffix}",
            type="standard",
            auth_mode=auth_mode,
            description="launch contract gate test",
            created_by=creator.id,
        )
        db_session.add(project)
        db_session.flush()
        version = Version(project_id=project.id, version_number="v0.1.0", name="v0.1.0")
        db_session.add(version)
        db_session.flush()
        db_session.add(
            PipelineState(
                version_id=version.id,
                flow_type="new_version",
                current_stage="done",
                current_actor="auditor",
                status="done",
                next_action="",
            )
        )
        db_session.flush()
        # A PASS with no verified_sha → provenance 'unbound' → verified, so the deploy reaches the gate
        # under test rather than tripping the reality-anchor guard first.
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
        customer = Customer(project_id=project.id, name="ICC", slug=f"icc{suffix}", subdomain=f"icc{suffix}")
        db_session.add(customer)
        db_session.flush()
        return customer, project

    return _seed


class TestMissingLaunchEnvKeys:
    def test_declared_in_env_example_is_wireable(self, projects_root, tmp_path):
        _write_project(
            tmp_path,
            "app-env",
            env_example={k: "" for k in deploy_service.LAUNCH_ENV_KEYS},
            compose_backend_env={},
        )
        assert deploy_service.missing_launch_env_keys("app-env") == []

    def test_declared_in_compose_is_wireable(self, projects_root, tmp_path):
        """The REAL shape of both live token-launch apps: the launch vars live in the backend service's
        ``environment:``, not in ``.env.example``. A check that only read the env file would have blocked
        nex-shopify and nex-websites — working deploys broken to prevent a broken one."""
        _write_project(
            tmp_path,
            "app-compose",
            env_example={"LOG_LEVEL": "info"},
            compose_backend_env={
                "MANAGER_LAUNCH_SIGNING_KEY": "${MANAGER_LAUNCH_SIGNING_KEY:-}",
                "MANAGER_MODULE_SLUG": "${MANAGER_MODULE_SLUG:-app-compose}",
                "MANAGER_DEPLOY_SLUG": "${MANAGER_DEPLOY_SLUG:-}",
            },
        )
        assert deploy_service.missing_launch_env_keys("app-compose") == []

    def test_app_naming_it_its_own_way_is_reported(self, projects_root, tmp_path):
        """The actual incident: the app declares the same CONCEPT under its own name. The provisioner keys
        off the exact standard names, so this app can never be wired — name precisely what is missing."""
        _write_project(
            tmp_path,
            "app-own-name",
            env_example={"NEX_MANAGER_LAUNCH_KEY": "", "LOG_LEVEL": "info"},
            compose_backend_env={"NEX_MANAGER_LAUNCH_KEY": "${NEX_MANAGER_LAUNCH_KEY:-}"},
        )
        assert deploy_service.missing_launch_env_keys("app-own-name") == list(deploy_service.LAUNCH_ENV_KEYS)

    def test_partial_declaration_reports_only_the_gap(self, projects_root, tmp_path):
        _write_project(
            tmp_path,
            "app-partial",
            env_example={},
            compose_backend_env={
                "MANAGER_LAUNCH_SIGNING_KEY": "",
                "MANAGER_MODULE_SLUG": "app-partial",
            },
        )
        assert deploy_service.missing_launch_env_keys("app-partial") == ["MANAGER_DEPLOY_SLUG"]

    def test_unreadable_project_fails_OPEN(self, projects_root):
        """We must not block a deploy on a guess. When nothing is readable the post-deploy proof decides —
        it judges on evidence instead of inference."""
        assert deploy_service.missing_launch_env_keys("no-such-project") == []


class TestUpFrontGate:
    """The gate lives in ``deploy()`` and fires BEFORE any teardown, so a refused deploy leaves the
    customer's running instance untouched."""

    async def test_token_app_without_the_contract_is_refused_with_the_missing_names(
        self, db_session, projects_root, tmp_path, deploy_fixture
    ):
        customer, project = deploy_fixture(auth_mode="token")
        _write_project(tmp_path, project.slug, env_example={"LOG_LEVEL": "info"}, compose_backend_env={})

        with pytest.raises(ValueError) as exc:
            await deploy_service.deploy(
                db_session,
                customer.id,
                version_number="v0.1.0",
                environment="uat",
                actor_id=None,
                deploy_runner=_never_called_runner,
            )

        message = str(exc.value)
        assert "jedným kliknutím" in message  # plain Slovak, no jargon
        for key in deploy_service.LAUNCH_ENV_KEYS:
            assert key in message  # names exactly what to add

    async def test_password_app_is_never_gated_on_the_launch_contract(
        self, db_session, projects_root, tmp_path, deploy_fixture
    ):
        """A password app has no launch ticket — the contract is irrelevant and must not block it."""
        customer, project = deploy_fixture(auth_mode="password")
        _write_project(tmp_path, project.slug, env_example={"LOG_LEVEL": "info"}, compose_backend_env={})

        event, url, _ = await deploy_service.deploy(
            db_session,
            customer.id,
            version_number="v0.1.0",
            environment="uat",
            actor_id=None,
            deploy_runner=_ok_runner,
        )
        assert event.status == "ok"
        assert url is not None


async def _never_called_runner(**kwargs):  # pragma: no cover - must never run
    raise AssertionError("the gate must refuse BEFORE the runner touches the instance")


async def _ok_runner(**kwargs):
    return True, "OK (faked)", "https://uat-x.isnex.eu"


class TestPostDeployLaunchProof:
    """The launch proof mints a ticket exactly as "Spustiť" does. What it finds is a WIRING gap, not a
    deploy failure — the app is built, up and serving — so it warns instead of failing (2026-07-28)."""

    def _wire(self, monkeypatch, wired: bool) -> None:
        monkeypatch.setattr(deploy_service, "uat_launch_wired", lambda *a, **k: wired)

    async def test_unwired_launch_is_a_SUCCESSFUL_deploy_carrying_a_warning(
        self, db_session, projects_root, tmp_path, deploy_fixture, monkeypatch
    ):
        """The pairing is missing, not the app. Recording ``failed`` here was a dead end of our own making:
        it also barred Akceptovať (see the test below), and nothing in the cockpit creates the pairing."""
        customer, project = deploy_fixture(auth_mode="token")
        _write_project(
            tmp_path,
            project.slug,
            env_example={},
            compose_backend_env={k: "" for k in deploy_service.LAUNCH_ENV_KEYS},
        )
        self._wire(monkeypatch, False)

        outcome = await deploy_service.deploy(
            db_session,
            customer.id,
            version_number="v0.1.0",
            environment="uat",
            actor_id=None,
            deploy_runner=_ok_runner,
        )
        event, url, _ = outcome

        assert event.status == "ok"  # the app deployed and serves — that is what a deploy status reports
        assert url is not None  # and the manager gets the link to it
        assert outcome.warnings, "the missing pairing must be reported, not swallowed"
        message = outcome.warnings[0]
        assert "NEX Manager" in message  # names exactly what is missing
        assert "nasaď" in message.lower()  # and what to do about it
        assert message in (event.detail or "")  # persisted in the audit row too, not only in the response

    async def test_a_warned_deploy_can_still_be_accepted_so_PROD_stays_reachable(
        self, db_session, projects_root, tmp_path, deploy_fixture, monkeypatch
    ):
        """The whole point of the change. ``accept`` requires a SUCCESSFUL UAT deploy, and accepting is the
        only thing that opens PROD — so recording the unwired deploy as failed locked the customer out of
        PROD for good, from inside the cockpit, with no way to unstick it."""
        customer, project = deploy_fixture(auth_mode="token")
        _write_project(
            tmp_path,
            project.slug,
            env_example={},
            compose_backend_env={k: "" for k in deploy_service.LAUNCH_ENV_KEYS},
        )
        self._wire(monkeypatch, False)

        await deploy_service.deploy(
            db_session,
            customer.id,
            version_number="v0.1.0",
            environment="uat",
            actor_id=None,
            deploy_runner=_ok_runner,
        )
        accepted = deploy_service.accept(db_session, customer.id, "v0.1.0", None)

        assert accepted.event_type == "accept"
        assert deploy_service.is_accepted(db_session, customer.id, "v0.1.0") is True

    async def test_the_pairing_warning_is_reported_once_not_twice(
        self, db_session, projects_root, tmp_path, deploy_fixture, monkeypatch
    ):
        """A real deploy sees the same gap twice — the provisioner renders an empty key, then the launch
        proof cannot mint against it. The manager must read about it once."""
        customer, project = deploy_fixture(auth_mode="token")
        _write_project(
            tmp_path,
            project.slug,
            env_example={},
            compose_backend_env={k: "" for k in deploy_service.LAUNCH_ENV_KEYS},
        )
        self._wire(monkeypatch, False)
        pairing_warning = uat_provisioner.launch_pairing_warning(deploy_service._customer_dir_slug(customer))

        async def _runner_that_already_warned(**kwargs):
            # Mirrors the real runner: the provisioner's warnings go into BOTH the detail and the list.
            return deploy_service.RunnerResult(
                True, f"OK (faked) | warnings: {pairing_warning}", "https://uat-x.isnex.eu", [pairing_warning]
            )

        outcome = await deploy_service.deploy(
            db_session,
            customer.id,
            version_number="v0.1.0",
            environment="uat",
            actor_id=None,
            deploy_runner=_runner_that_already_warned,
        )

        assert outcome.warnings.count(pairing_warning) == 1
        assert (outcome[0].detail or "").count(pairing_warning) == 1

    async def test_launchable_uat_stays_green(self, db_session, projects_root, tmp_path, deploy_fixture, monkeypatch):
        customer, project = deploy_fixture(auth_mode="token")
        _write_project(
            tmp_path,
            project.slug,
            env_example={},
            compose_backend_env={k: "" for k in deploy_service.LAUNCH_ENV_KEYS},
        )
        self._wire(monkeypatch, True)

        event, url, _ = await deploy_service.deploy(
            db_session,
            customer.id,
            version_number="v0.1.0",
            environment="uat",
            actor_id=None,
            deploy_runner=_ok_runner,
        )

        assert event.status == "ok"
        assert url is not None

    async def test_password_app_is_not_launch_checked(
        self, db_session, projects_root, tmp_path, deploy_fixture, monkeypatch
    ):
        """A password app is opened with a login — minting a ticket is meaningless, so an unwired result
        must never fail its deploy."""
        customer, project = deploy_fixture(auth_mode="password")
        _write_project(tmp_path, project.slug, env_example={}, compose_backend_env={})

        def _must_not_run(*a, **k):  # pragma: no cover - asserts absence
            raise AssertionError("a password app must never be launch-checked")

        monkeypatch.setattr(deploy_service, "uat_launch_wired", _must_not_run)

        event, _url, _ = await deploy_service.deploy(
            db_session,
            customer.id,
            version_number="v0.1.0",
            environment="uat",
            actor_id=None,
            deploy_runner=_ok_runner,
        )
        assert event.status == "ok"

    async def test_a_failed_deploy_is_not_re_judged_by_the_launch_proof(
        self, db_session, projects_root, tmp_path, deploy_fixture, monkeypatch
    ):
        """A deploy that already failed reads red for its OWN reason — the launch proof must not run and
        overwrite that cause with a launch message."""
        customer, project = deploy_fixture(auth_mode="token")
        _write_project(
            tmp_path,
            project.slug,
            env_example={},
            compose_backend_env={k: "" for k in deploy_service.LAUNCH_ENV_KEYS},
        )

        def _must_not_run(*a, **k):  # pragma: no cover - asserts absence
            raise AssertionError("the launch proof must not run on an already-failed deploy")

        monkeypatch.setattr(deploy_service, "uat_launch_wired", _must_not_run)

        async def _failing_runner(**kwargs):
            return False, "provision failed (faked)", None

        event, url, _ = await deploy_service.deploy(
            db_session,
            customer.id,
            version_number="v0.1.0",
            environment="uat",
            actor_id=None,
            deploy_runner=_failing_runner,
        )
        assert event.status == "failed"
        assert "provision failed" in (event.detail or "")
        assert url is None


class TestWarningsChannel:
    """``DeployResult.warnings`` has existed since the schema was written and nothing ever filled it, so the
    provisioner's notes — an app with no frontend service, a DB driver that could not be verified, a missing
    NEX Manager pairing — reached the manager only inside a ``detail`` string the screen shows on FAILURE. On
    the successful deploys they are actually about, they were thrown away."""

    async def test_provisioning_warnings_reach_the_caller(
        self, db_session, projects_root, tmp_path, deploy_fixture, monkeypatch
    ):
        customer, project = deploy_fixture(auth_mode="password")
        _write_project(tmp_path, project.slug, env_example={}, compose_backend_env={})

        async def _warning_runner(**kwargs):
            return deploy_service.RunnerResult(
                True, "OK (faked)", "https://uat-x.isnex.eu", ["v appke nie je frontend — beží len /api"]
            )

        outcome = await deploy_service.deploy(
            db_session,
            customer.id,
            version_number="v0.1.0",
            environment="uat",
            actor_id=None,
            deploy_runner=_warning_runner,
        )

        assert outcome.warnings == ["v appke nie je frontend — beží len /api"]
        assert outcome[0].status == "ok"  # a warning is a note about a success, never a failure

    async def test_a_runner_returning_a_plain_triple_still_works(
        self, db_session, projects_root, tmp_path, deploy_fixture
    ):
        """The runner seam stays a 3-tuple: every injected/faked runner in the suite returns one, and the
        warnings ride along as an attribute rather than a fourth element nobody unpacks."""
        customer, project = deploy_fixture(auth_mode="password")
        _write_project(tmp_path, project.slug, env_example={}, compose_backend_env={})

        outcome = await deploy_service.deploy(
            db_session,
            customer.id,
            version_number="v0.1.0",
            environment="uat",
            actor_id=None,
            deploy_runner=_ok_runner,
        )
        event, url, bumped_to = outcome  # the historic 3-way unpack, unchanged

        assert outcome.warnings == []
        assert (event.status, bool(url), bumped_to) == ("ok", True, None)

    def test_the_default_runner_carries_the_provisioners_warnings(self, monkeypatch):
        """The real runner's own seam: whatever ``provision_uat`` warned about comes back on the result AND
        stays in the recorded detail (the audit row keeps its full story)."""
        import asyncio

        from backend.services import orchestrator as orch

        class _Result:
            warnings = ["chýba spárovaný NEX Manager"]
            fe_service = "frontend"

        async def _fake_up(*a, **k):
            return True, "OK"

        monkeypatch.setattr(uat_provisioner, "provision_uat", lambda *a, **k: _Result())
        monkeypatch.setattr(orch, "_run_uat_deploy", _fake_up)

        result = asyncio.run(
            deploy_service._default_deploy_runner(
                project_slug="nex-shopify", uat_slug="andros-uat", version_number="v0.1.0", force_fresh=False
            )
        )
        ok, detail, url = result

        assert ok is True
        assert result.warnings == ["chýba spárovaný NEX Manager"]
        assert "chýba spárovaný NEX Manager" in detail
        assert url == "https://uat-andros-shopify.isnex.eu"

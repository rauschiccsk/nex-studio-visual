"""DEV-416 — a version is offered, deployed and re-checked only while the code a deploy would build is its own code.

17.11.2026, NEX Manager: customers ran 1.6.0, but „Nasadiť verziu“ offered and preselected the released 1.2.2 — a
released version was verified by definition, whatever HEAD held, and a deploy builds HEAD: 127 commits of later
work (the 1.7.0 in progress among them) would have gone out under the number 1.2.2. Once it is gone from the offer,
„Over znova“ on 1.6.0 would check 1.7.0's unfinished work under 1.6.0's number. Director: „súhlasím“ (the guard
here; deploying a version from its own commit is a ticket of its own).

The repository is real: the rule asks git what changed between the version's commit and HEAD.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.routes.pipeline import _board
from backend.core.errors import ConflictError
from backend.db.models.deploy import DeployEvent
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.schemas.customer import CustomerCreate
from backend.services import ci_status, claude_agent, deploy_source, orchestrator
from backend.services import customer as customer_service
from backend.services import deploy as deploy_service


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _commit(repo: Path, files: dict[str, str | None], message: str) -> str:
    for name, text in files.items():
        path = repo / name
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


class _Project:
    """A project with a real repository under the projects root, and its versions in the database."""

    def __init__(self, db, root: Path) -> None:
        self.db = db
        owner = User(
            username=f"u{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local", password_hash="x", role="ri"
        )
        db.add(owner)
        db.flush()
        self.owner = owner
        self.project = Project(
            name="NEX Manager",
            slug=f"nm-{uuid.uuid4().hex[:8]}",
            type="standard",
            auth_mode="password",
            description="DEV-416",
            created_by=owner.id,
        )
        db.add(self.project)
        db.flush()
        self.repo = root / self.project.slug
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "t")

    def version(self, number: str, *, status: str, anchor: str | None = None, stage: str = "done") -> Version:
        version = Version(project_id=self.project.id, version_number=number, name=number, status=status)
        self.db.add(version)
        self.db.flush()
        self.db.add(
            PipelineState(
                version_id=version.id,
                flow_type="new_version",
                current_stage=stage,
                current_actor="auditor",
                status="done" if stage == "done" else "awaiting_manazer",
                next_action="",
            )
        )
        self.db.flush()
        if anchor is not None:
            orchestrator._record_message(
                self.db,
                version_id=version.id,
                stage="verifikacia",
                author="auditor",
                recipient="manazer",
                kind="verdict",
                content="PASS",
                payload={"verdict": "PASS", "phase": "verifikacia", "verified_sha": anchor},
            )
            self.db.flush()
        return version


@pytest.fixture
def nex_manager(db_session, tmp_path, monkeypatch) -> _Project:
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    return _Project(db_session, tmp_path)


def _released_then_its_notes_moved(p: _Project) -> tuple[Version, str]:
    anchor = _commit(
        p.repo, {"backend/app.py": "v = '1.2.2'\n", "docs/specs/versions/1.2.2/RELEASE_NOTES.md": "# 1.2.2\n"}, "1.2.2"
    )
    _commit(
        p.repo,
        {
            "docs/specs/versions/1.2.2/RELEASE_NOTES.md": None,
            "docs/specs/versions/v1.0.0/RELEASE_NOTES.md": "# 1.2.2\n",
        },
        "first PROD: the release note moves",
    )
    return p.version("1.2.2", status="released", anchor=anchor), anchor


# --- a released version ------------------------------------------------------------------------------------------


def test_a_released_version_stays_verified_after_its_release_note_moves(db_session, nex_manager) -> None:
    """The first-PROD note move must not hard-block the 2nd customer's deploy of the just-released version."""
    released, _ = _released_then_its_notes_moved(nex_manager)

    assert orchestrator.version_verified(db_session, released.id) == (True, "released")


def test_a_released_version_whose_code_moved_is_not_verified(db_session, nex_manager) -> None:
    released, _ = _released_then_its_notes_moved(nex_manager)
    _commit(nex_manager.repo, {"backend/app.py": "v = 'later'\n"}, "maintenance")

    assert orchestrator.version_verified(db_session, released.id) == (False, "sha_drift")


def test_a_released_version_with_a_newer_version_in_the_code_is_superseded(db_session, nex_manager) -> None:
    released, _ = _released_then_its_notes_moved(nex_manager)
    nex_manager.version("1.7.0", status="active", stage="vizual")
    _commit(nex_manager.repo, {"backend/app.py": "v = '1.7.0 in progress'\n"}, "1.7.0 work")

    assert orchestrator.version_verified(db_session, released.id) == (False, "superseded")


def test_a_released_version_without_a_commit_on_record_falls_back_to_its_tag(db_session, nex_manager) -> None:
    anchor = _commit(nex_manager.repo, {"backend/app.py": "v = '1.2.2'\n"}, "1.2.2")
    _git(nex_manager.repo, "tag", "v1.2.2", anchor)
    released = nex_manager.version("1.2.2", status="released", anchor="legacy")
    assert orchestrator.version_verified(db_session, released.id) == (True, "released")

    _commit(nex_manager.repo, {"backend/app.py": "v = 'later'\n"}, "later")
    assert orchestrator.version_verified(db_session, released.id) == (False, "sha_drift")


def test_a_released_version_with_no_check_at_all_is_not_offered(db_session, nex_manager) -> None:
    _commit(nex_manager.repo, {"backend/app.py": "v = 1\n"}, "code")
    released = nex_manager.version("1.2.2", status="released")

    assert orchestrator.version_verified(db_session, released.id) == (False, "released_unanchored")


def test_a_released_version_that_passed_while_the_repository_could_not_be_read_reads_as_unconfirmed(
    db_session, nex_manager
) -> None:
    """Its PASS was never bound to a commit — our own read failure, which never un-verifies (any version: ``unbound``,
    the screen says the check cannot be confirmed). A first PROD deploy of such a version must not lock out the
    next customer."""
    _commit(nex_manager.repo, {"backend/app.py": "v = 1\n"}, "code")
    released = nex_manager.version("1.0.0", status="released")
    orchestrator._record_message(
        db_session,
        version_id=released.id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content="PASS",
        payload={"verdict": "PASS", "phase": "verifikacia"},
    )
    db_session.flush()

    assert orchestrator.version_verified(db_session, released.id) == (True, "unbound")


# --- a finished version once a newer one has begun ---------------------------------------------------------------


def test_a_finished_version_is_superseded_once_a_newer_versions_code_is_in(db_session, nex_manager) -> None:
    anchor = _commit(nex_manager.repo, {"backend/app.py": "v = '1.6.0'\n"}, "1.6.0")
    done = nex_manager.version("1.6.0", status="done", anchor=anchor)
    nex_manager.version("1.7.0", status="active", stage="vizual")

    _commit(nex_manager.repo, {"docs/specs/versions/1.7.0/specification.md": "# 1.7.0\n"}, "1.7.0 specification")
    assert orchestrator.version_verified(db_session, done.id) == (False, "sha_drift"), "a specification is not code"

    _commit(nex_manager.repo, {"backend/app.py": "v = '1.7.0 in progress'\n"}, "1.7.0 work")
    assert orchestrator.version_verified(db_session, done.id) == (False, "superseded")


def test_a_planned_newer_version_does_not_supersede(db_session, nex_manager) -> None:
    anchor = _commit(nex_manager.repo, {"backend/app.py": "v = '1.6.0'\n"}, "1.6.0")
    done = nex_manager.version("1.6.0", status="done", anchor=anchor)
    planned = Version(project_id=nex_manager.project.id, version_number="1.7.0", name="1.7.0", status="planned")
    db_session.add(planned)
    db_session.flush()
    _commit(nex_manager.repo, {"backend/app.py": "v = 'maintenance'\n"}, "maintenance")

    assert orchestrator.version_verified(db_session, done.id) == (False, "sha_drift")


def _the_17_november_project(p: _Project) -> tuple[Version, Version, Version]:
    """NEX Manager on 17.11.2026: 1.2.2 released, 1.6.0 finished, 1.7.0 in its Vizuál with its work in the code."""
    released, _ = _released_then_its_notes_moved(p)
    anchor = _commit(p.repo, {"backend/app.py": "v = '1.6.0'\n"}, "1.6.0")
    finished = p.version("1.6.0", status="done", anchor=anchor)
    newer = p.version("1.7.0", status="active", stage="vizual")
    _commit(p.repo, {"backend/app.py": "v = '1.7.0 in progress'\n"}, "1.7.0 work")
    return released, finished, newer


def test_while_a_newer_version_is_built_every_finished_version_is_offered(db_session, nex_manager) -> None:
    """DEV-420 (Director 17.11.2026: „Súhlasím“): a deploy builds the version's own commit, so a version stays on offer
    while the project's code moves on — 1.6.0 during the work on 1.7.0. Until DEV-420 nothing was offered."""
    nex_manager.version("0.9.0", status="done")  # finished, but no commit on record: nothing to build
    _the_17_november_project(nex_manager)
    p = nex_manager.project

    offer = deploy_service.list_verified_versions(db_session, p.id)

    assert offer == ["1.6.0", "1.2.2"]
    assert deploy_service.deployability(db_session, p, verified_versions=offer, user=nex_manager.owner)["cause"] == "ok"


def _customer(db, p: _Project, slug: str = "skit"):
    return customer_service.create(db, p.project.id, CustomerCreate(name=slug.upper(), slug=slug, subdomain=slug))


#: Where the deploys handed their runners the project — gone once the deploy is over.
exports: list = []


def _runner_reading_the_source(seen: list):
    """A runner that reads what a real one renders from — the project's app file in the source it is handed."""

    async def _runner(**kwargs):
        source = kwargs["source"]
        seen.append((source.commit, (source.path / "backend" / "app.py").read_text()))
        exports.append(source.path)
        return True, "OK", "https://uat-skit.isnex.eu"

    return _runner


@pytest.mark.asyncio
async def test_an_older_version_is_deployed_from_its_own_commit_not_from_the_project_folder(
    db_session, nex_manager, monkeypatch
) -> None:
    released, _finished, _newer = _the_17_november_project(nex_manager)
    anchor = orchestrator.deployable_commit(db_session, released.id)[0]
    asked: list = []

    async def _ci(root, sha=None, *, cerstvy=False):
        asked.append(sha)
        return ci_status.StavZostavenia("green", "CI zelené", sha, 0.0)

    monkeypatch.setattr(ci_status, "stav_commitu", _ci)
    seen: list = []

    event, _url, _bumped = await deploy_service.deploy(
        db_session,
        _customer(db_session, nex_manager).id,
        version_number="1.2.2",
        environment="uat",
        actor_id=nex_manager.owner.id,
        deploy_runner=_runner_reading_the_source(seen),
    )

    assert event.status == "ok"
    assert seen == [(anchor, "v = '1.2.2'\n")], "the deploy read the project's folder (1.7.0 in progress)"
    assert asked == [anchor], "the checks (CI) of another commit were asked"
    assert (nex_manager.repo / "backend" / "app.py").read_text() == "v = '1.7.0 in progress'\n"
    assert not exports[-1].exists(), "the export outlived the deploy"


@pytest.mark.asyncio
async def test_the_default_runner_renders_from_the_source_and_builds_its_commit(monkeypatch, tmp_path) -> None:
    seen: dict = {}

    def _provision(*args, **kwargs):
        seen["source_path"] = kwargs.get("source_path")
        raise ValueError("rendered — stop here")

    monkeypatch.setattr(deploy_service.uat_provisioner, "provision_uat", _provision)
    source = deploy_source.DeploySource("abc123", tmp_path / "nex-manager")

    outcome = await deploy_service._default_deploy_runner(
        project_slug="nex-manager", uat_slug="icc-uat", version_number="1.6.0", force_fresh=False, source=source
    )

    assert seen == {"source_path": tmp_path / "nex-manager"}
    assert outcome[0] is False

    async def _built(*args, **kwargs):
        seen["commit"] = kwargs.get("commit")
        return False, "stop"

    monkeypatch.setattr(deploy_service.uat_provisioner, "provision_uat", lambda *a, **k: _rendered(tmp_path))
    monkeypatch.setattr(orchestrator, "instance_health", _no_health)
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _built)
    await deploy_service._default_deploy_runner(
        project_slug="nex-manager", uat_slug="icc-uat", version_number="1.6.0", force_fresh=False, source=source
    )

    assert seen["commit"] == "abc123"

    async def _built_prod(*args, **kwargs):
        seen["prod_commit"] = kwargs.get("commit")
        return False, "stop"

    monkeypatch.setattr(orchestrator, "_run_prod_deploy", _built_prod)
    await deploy_service._default_deploy_runner(
        project_slug="nex-manager", uat_slug="icc-prod", version_number="1.6.0", force_fresh=False, source=source
    )

    assert seen["prod_commit"] == "abc123"


async def _no_health(*_a, **_k):
    return None


def _rendered(tmp_path):
    compose = tmp_path / "uat" / "docker-compose.yml"
    compose.parent.mkdir(parents=True, exist_ok=True)
    compose.write_text("services: {}\n", encoding="utf-8")
    return SimpleNamespace(compose_path=compose, fe_service=None, warnings=[])  # what the runner reads off the render


def _running(db, customer, version_number: str, environment: str) -> None:
    db.add(
        DeployEvent(
            customer_id=customer.id,
            project_id=customer.project_id,
            environment=environment,
            version_number=version_number,
            event_type="deploy",
            status="ok",
            actor_id=None,
        )
    )
    db.flush()


@pytest.mark.asyncio
async def test_prod_never_goes_back_to_an_older_version(db_session, nex_manager) -> None:
    _the_17_november_project(nex_manager)
    customer = _customer(db_session, nex_manager)
    _running(db_session, customer, "1.6.0", "prod")
    seen: list = []

    with pytest.raises(ConflictError) as refused:
        await deploy_service.deploy(
            db_session,
            customer.id,
            version_number="1.2.2",
            environment="prod",
            actor_id=nex_manager.owner.id,
            deploy_runner=_runner_reading_the_source(seen),
        )

    assert str(refused.value) == (
        "Nasadenie zastavené: u zákazníka beží novšia verzia 1.6.0. Staršia verzia 1.2.2 nepozná jej databázu a "
        "nenaštartovala by — návrat na staršiu verziu sa na PROD nerobí."
    )
    assert seen == []


@pytest.mark.asyncio
async def test_uat_goes_back_only_built_anew(db_session, nex_manager, monkeypatch) -> None:
    _the_17_november_project(nex_manager)
    customer = _customer(db_session, nex_manager)
    _running(db_session, customer, "1.6.0", "uat")
    monkeypatch.setattr(deploy_service.uat_provisioner, "compose_down_with_volumes", lambda path, **_: (True, "OK"))
    seen: list = []
    run = dict(
        version_number="1.2.2",
        environment="uat",
        actor_id=nex_manager.owner.id,
        deploy_runner=_runner_reading_the_source(seen),
    )

    with pytest.raises(ConflictError, match="nasadíš ju len cez „Postaviť nanovo“"):
        await deploy_service.deploy(db_session, customer.id, **run)
    await deploy_service.deploy(db_session, customer.id, force_fresh=True, **run)

    assert [code for _commit, code in seen] == ["v = '1.2.2'\n"]


def test_a_version_founded_before_the_first_prod_is_newer_code_despite_its_number(db_session, nex_manager) -> None:
    """A first PROD deploy renames its version to 1.0.0; 0.2.0, founded before it, keeps its number with newer code.
    The commits decide what is older, not the numbers."""
    first = _commit(nex_manager.repo, {"backend/app.py": "v = 'first'\n"}, "0.1.0")
    nex_manager.version("1.0.0", status="released", anchor=first)
    later = _commit(nex_manager.repo, {"backend/app.py": "v = 'second'\n"}, "0.2.0")
    nex_manager.version("0.2.0", status="done", anchor=later)
    p = nex_manager.project

    assert deploy_service.older_than_running(db_session, p, ["1.0.0", "0.2.0"], "1.0.0") == []
    assert deploy_service.older_than_running(db_session, p, ["1.0.0", "0.2.0"], "0.2.0") == ["1.0.0"]


def test_a_version_without_its_commit_is_not_deployable_and_says_why(db_session, nex_manager) -> None:
    _commit(nex_manager.repo, {"backend/app.py": "v = 1\n"}, "code")
    unbound = nex_manager.version("1.0.0", status="done")
    orchestrator._record_message(
        db_session,
        version_id=unbound.id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content="PASS",
        payload={"verdict": "PASS", "phase": "verifikacia"},
    )
    gone = nex_manager.version("1.1.0", status="done", anchor="0" * 40)

    assert orchestrator.deployable_commit(db_session, unbound.id) == (None, "no_commit")
    assert orchestrator.deployable_commit(db_session, gone.id) == (None, "commit_missing")
    assert deploy_service.list_verified_versions(db_session, nex_manager.project.id) == []


@pytest.mark.asyncio
async def test_the_finished_version_cannot_be_checked_again_on_the_newer_code(db_session, nex_manager) -> None:
    _, finished, _ = _the_17_november_project(nex_manager)

    board = _board(db_session, finished.id)
    assert board.verified_provenance == "superseded"
    assert "overit_znovu" not in board.available_actions

    with pytest.raises(orchestrator.OrchestratorError) as refused:
        await orchestrator.apply_action(db_session, version_id=finished.id, action="overit_znovu", payload={})
    assert str(refused.value) == (
        "Verziu 1.6.0 už znova overiť nejde: kód projektu už obsahuje prácu na verzii 1.7.0 a overenie by prebehlo "
        "na ňom. K zákazníkovi sa nasadzuje novšia verzia."
    )

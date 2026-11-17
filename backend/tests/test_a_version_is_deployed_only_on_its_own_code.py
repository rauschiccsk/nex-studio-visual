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

import pytest

from backend.api.routes.pipeline import _board
from backend.core.errors import ConflictError
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.schemas.customer import CustomerCreate
from backend.services import claude_agent, orchestrator
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


def test_a_released_version_with_no_commit_at_all_is_not_offered(db_session, nex_manager) -> None:
    _commit(nex_manager.repo, {"backend/app.py": "v = 1\n"}, "code")
    released = nex_manager.version("1.2.2", status="released")

    assert orchestrator.version_verified(db_session, released.id) == (False, "released_unanchored")


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


def test_nothing_is_offered_and_the_screen_names_the_newer_version(db_session, nex_manager) -> None:
    _, finished, newer = _the_17_november_project(nex_manager)
    p = nex_manager.project

    offer = deploy_service.list_verified_versions(db_session, p.id)
    block = deploy_service.deployability(db_session, p, verified_versions=offer, user=nex_manager.owner)

    assert offer == []
    assert block == {
        "cause": "superseded",
        "version_number": "1.6.0",
        "version_id": finished.id,
        "can_reverify": False,
        "next_version_id": newer.id,
        "next_version_number": "1.7.0",
    }


@pytest.mark.asyncio
async def test_a_deploy_of_the_released_version_is_refused(db_session, nex_manager) -> None:
    _the_17_november_project(nex_manager)
    customer = customer_service.create(
        db_session, nex_manager.project.id, CustomerCreate(name="SKIT", slug="skit", subdomain="skit", secret=None)
    )
    calls: list = []

    async def _runner(**kwargs):
        calls.append(kwargs)
        return True, "OK", "https://uat-skit.isnex.eu"

    with pytest.raises(ConflictError) as refused:
        await deploy_service.deploy(
            db_session,
            customer.id,
            version_number="1.2.2",
            environment="uat",
            actor_id=nex_manager.owner.id,
            deploy_runner=_runner,
        )

    assert str(refused.value) == (
        "Nasadenie zastavené: kód projektu už obsahuje prácu na verzii 1.7.0 — nasadil by sa jej kód pod číslom "
        "1.2.2. Nasadiť sa dá novšia verzia, keď je hotová."
    )
    assert calls == []


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

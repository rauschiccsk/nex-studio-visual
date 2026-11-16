"""DEV-413 — the Staviteľ and the project's Poradca read the documentation of the cockpit's other projects themselves.

16.11.2026 the Staviteľ of NEX HelpDesk stopped in Príprava asking the Manažér to attach the description of NEX
Knowledge's interface for other applications; the project's Poradca, asked how to answer, sent the Manažér to download
it by hand and — reading only the database schema in the Knowledge Base — claimed a discrepancy the interface does not
have. The document was in the cockpit all along, and the Projektant had read it. Director: „Riešením nie je, že ty
budeš vkladať dokumenty, ktoré sú k dispozícii v kokpite. Prečo nemôže Staviteľ pozrieť čo potrebuje?“ — „Áno“ for the
Poradca too.

What a project's documentation is stays the Projektant's rule: README.md and .md under docs/ — never code, never the
other files lying in docs/, never a secret, never the attachments in private/.
"""

from __future__ import annotations

import asyncio
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from backend.config.settings import settings
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import build_sandbox, claude_agent, project_docs
from backend.services.poradca import idea_tools, runner, tools

TOKEN = "abcdef123456"


class _Ctx:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *exc):
        return False


def _user(db: Any) -> User:
    u = User(
        username=f"u_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="x", role="shu"
    )
    db.add(u)
    db.flush()
    return u


def _project(db: Any, owner: User, slug: str) -> Project:
    p = Project(name=slug, slug=slug, type="standard", auth_mode="password", description="d", created_by=owner.id)
    db.add(p)
    db.flush()
    return p


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def world(db_session, tmp_path, monkeypatch):
    """Two projects of the cockpit: the one being built (``helpdesk``) and the one it stands on (``knowledge``), whose
    folder holds documentation and everything that is not documentation."""
    projects = tmp_path / "projects"
    copies = tmp_path / "project-docs"
    copies.mkdir()
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects)
    monkeypatch.setattr(settings, "project_docs_dir", str(copies))
    for module in (project_docs, idea_tools, tools):
        monkeypatch.setattr(module, "SessionLocal", lambda: _Ctx(db_session))
    me = _user(db_session)
    suffix = uuid.uuid4().hex[:6]
    own = _project(db_session, me, f"helpdesk-{suffix}")
    other = _project(db_session, _user(db_session), f"knowledge-{suffix}")

    k = projects / other.slug
    _write(k / "README.md", "# NEX Knowledge")
    _write(k / "docs" / "API_PRE_APLIKACIE.md", "# Rozhranie\n`text_status`: `ready`")
    _write(k / "docs" / "specs" / "v1.1.0" / "specification.md", "## 7. Rozhranie pre aplikácie")
    _write(k / "docs" / "mail.eml", "From: zakaznik@example.com")
    _write(k / "docs" / "export.csv", "meno;telefon")
    _write(k / "docs" / ".env.md", "TOKEN=x")
    _write(k / "backend" / "app.py", "SECRET = 1")
    _write(k / "private" / "priloha.md", "# Príloha zákazníka")
    outside = tmp_path / "outside.md"
    _write(outside, "OUTSIDE")
    (k / "docs" / "odkaz.md").symlink_to(outside)
    _write(projects / own.slug / "docs" / "zadanie.md", "# Zadanie HelpDesk")
    return {"own": own, "other": other, "me": me, "copies": copies, "outside": outside}


def _files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() or p.is_symlink()}


# --- the copy the Staviteľ gets ---------------------------------------------------------------------------------


def test_the_copy_holds_the_other_projects_documentation_and_nothing_else(world):
    copy = Path(project_docs.snapshot(world["own"].slug, TOKEN))
    k = world["other"].slug

    assert copy == world["copies"] / f"turn-{TOKEN}"
    assert _files(copy) == {
        f"{k}/README.md",
        f"{k}/docs/API_PRE_APLIKACIE.md",
        f"{k}/docs/specs/v1.1.0/specification.md",
    }
    api = (copy / k / "docs" / "API_PRE_APLIKACIE.md").read_text(encoding="utf-8")
    assert api == "# Rozhranie\n`text_status`: `ready`"
    assert not any(p.is_symlink() for p in copy.rglob("*"))
    assert "OUTSIDE" not in {p.read_text(encoding="utf-8") for p in copy.rglob("*") if p.is_file()}


def test_the_sandbox_user_can_read_every_file_of_the_copy(world):
    """The backend copies as root; the sandbox reads as uid 1000 — whatever umask the backend runs with."""
    old = os.umask(0o077)
    try:
        copy = Path(project_docs.snapshot(world["own"].slug, TOKEN))
    finally:
        os.umask(old)

    assert {stat.S_IMODE(p.stat().st_mode) for p in copy.rglob("*") if p.is_file()} == {0o644}
    assert {stat.S_IMODE(p.stat().st_mode) for p in [copy, *copy.rglob("*")] if p.is_dir()} == {0o755}


def test_a_turns_copy_is_removed_and_nothing_else_is(world, tmp_path):
    copy = project_docs.snapshot(world["own"].slug, TOKEN)
    keep = world["copies"] / "keep"
    keep.mkdir()
    elsewhere = tmp_path / "turn-abcdef654321"
    elsewhere.mkdir()

    for path in (str(keep), str(elsewhere), str(world["copies"]), str(world["copies"] / f"turn-{TOKEN}" / "..")):
        project_docs.remove(path)
    assert keep.is_dir() and elsewhere.is_dir() and Path(copy).is_dir()

    project_docs.remove(copy)
    assert not Path(copy).exists()


def test_without_its_folder_a_turn_gets_no_copy(world, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "project_docs_dir", str(tmp_path / "missing"))

    assert project_docs.snapshot(world["own"].slug, TOKEN) is None


def test_a_copy_that_fails_leaves_nothing_behind(world, monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(project_docs.shutil, "copyfile", broken)

    assert project_docs.snapshot(world["own"].slug, TOKEN) is None
    assert list(world["copies"].iterdir()) == []


def test_a_copy_left_by_a_crashed_turn_is_swept(world):
    stale, fresh = world["copies"] / "turn-111111111111", world["copies"] / "turn-222222222222"
    stale.mkdir()
    fresh.mkdir()
    old = time.time() - 2 * 24 * 3600
    os.utime(stale, (old, old))

    project_docs.snapshot(world["own"].slug, TOKEN)

    assert not stale.exists() and fresh.is_dir()


@pytest.mark.parametrize("token", ["../etc", "turn", "ABCDEF123456", ""])
def test_only_a_turn_token_names_a_copy(world, token):
    with pytest.raises(ValueError):
        project_docs.snapshot(world["own"].slug, token)


# --- the sandbox mounts the copy read-only, and nothing else of the other projects ------------------------------


def _sandbox_argv(monkeypatch, project_docs_path: str | None) -> list[str]:
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", Path("/opt/projects"))
    return build_sandbox.build_run_argv(
        project_slug="acme",
        container_name="nsv-build-acme-x",
        claude_argv=["claude", "-p", "zadanie"],
        project_docs=project_docs_path,
    )


def test_the_sandbox_mounts_the_copy_read_only_where_the_rules_say(world, monkeypatch):
    copy = project_docs.snapshot(world["own"].slug, TOKEN)

    argv = _sandbox_argv(monkeypatch, copy)

    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount" and project_docs.CONTAINER_DIR in argv[i + 1]]
    assert mounts == [f"type=bind,source={copy},target={project_docs.CONTAINER_DIR},readonly"]
    assert project_docs.CONTAINER_DIR not in " ".join(_sandbox_argv(monkeypatch, None))


@pytest.mark.parametrize("path", ["/opt/projects", "/opt/customers", "{copies}", "{copies}/keep", "{copies}/turn-x/.."])
def test_the_sandbox_mounts_nothing_but_a_turns_copy(world, monkeypatch, path):
    (world["copies"] / "keep").mkdir()

    with pytest.raises(build_sandbox.BuildSandboxUnavailable):
        _sandbox_argv(monkeypatch, path.format(copies=world["copies"]))


# --- the rules say where it is, from one place ------------------------------------------------------------------


_FOLDER = f"`{project_docs.CONTAINER_DIR}/<slug projektu>/`"


@pytest.mark.parametrize(
    "charter, sentences",
    [
        (
            "ai-agent-charter.md",
            [
                f"ostatných projektov kokpitu (read-only, {_FOLDER})",  # what the isolated container shows
                f"Návrhu, Vizuáli a Programovaní len na čítanie v {_FOLDER}",  # the rule…
                "Prečítaj si ju tam; Manažéra o ňu nežiadaj a nepros ho, aby ju priložil.",  # …and what it forbids
            ],
        ),
        (
            "poradca-charter.md",
            [
                "nástrojom `dokumentacia_projektu` a Manažéra ho neposielaj sťahovať ani prikladať.",
                f"Návrhu, Vizuáli a Programovaní sám, len na čítanie, v {_FOLDER}.",
            ],
        ),
    ],
)
def test_the_rules_name_the_folder_the_sandbox_mounts(charter, sentences):
    text = (Path(runner._CHARTER).parent / charter).read_text(encoding="utf-8")

    assert [sentence for sentence in sentences if sentence not in text] == []
    assert _FOLDER in project_docs.TURN_NOTE


# --- the project's Poradca reads the other project's documentation ----------------------------------------------


def _poradca_tool(world, name: str):
    built = tools.build_tools(project_id=world["own"].id, version_id=None, user_id=world["me"].id)
    return next(t for t in built if t.name == name)


def test_the_projects_poradca_lists_the_projects_and_reads_another_ones_documentation(world):
    k = world["other"].slug

    listed = asyncio.run(_poradca_tool(world, "projekty").handler({}))
    doc = asyncio.run(
        _poradca_tool(world, "dokumentacia_projektu").handler({"projekt": k, "cesta": "docs/API_PRE_APLIKACIE.md"})
    )

    assert f"({k})" in listed
    assert doc == "# Rozhranie\n`text_status`: `ready`"


@pytest.mark.parametrize(
    "wanted", ["backend/app.py", "private/priloha.md", "docs/mail.eml", "docs/odkaz.md", "docs/.env.md"]
)
def test_the_projects_poradca_reads_documentation_only(world, wanted):
    from backend.services.poradca.mcp_server import ToolError

    with pytest.raises(ToolError):
        asyncio.run(
            _poradca_tool(world, "dokumentacia_projektu").handler({"projekt": world["other"].slug, "cesta": wanted})
        )


def test_the_poradcas_rules_name_its_documentation_tools():
    charter = Path(runner._CHARTER).read_text(encoding="utf-8")

    assert "`projekty`, `dokumentacia_projektu`" in charter

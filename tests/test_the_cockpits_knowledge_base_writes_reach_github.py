"""DEV-94 — what the cockpit writes into the Knowledge Base is committed with only its own files and reaches GitHub.

12.10.2026: the approved database schema of NEX Inbox 1.8.0 was committed in the Knowledge Base on ANDROS and never
pushed — the copy on MAGER, which pulls from GitHub every 15 minutes, never saw it. Port blocks were written into
infrastructure/port-registry.yaml without a commit at all, and a deleted project's folder was removed from disk without
one. Somebody's unfinished work lies in the same working tree, so a commit carries only the cockpit's own paths, and
publishing never merges: when GitHub holds commits this tree lacks, that is said, not merged (Director 13.10.2026).

GitHub is a bare repository on disk here — the same git commands, no network.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

import pytest

from backend import main
from backend.api.routes import knowledge as knowledge_routes
from backend.config.settings import Settings, settings
from backend.services import kb_git, orchestrator, port_registry
from backend.services.database_schema import KnowledgeBaseSchemaPublisher
from backend.services.knowledge_base_writer import KnowledgeBaseWriter
from tests.integration.test_knowledge_rag import _build_client, _make_user
from tests.services.test_database_schema_publish import GOVERNANCE, INDEX
from tests.services.test_database_schema_publish import SCHEMA as APPROVED
from tests.test_database_schema_approval import _awaiting_navrh, _user
from tests.test_orchestrator_v2_navrh import _msgs

REGISTRY = (
    "aktualizované: 2026-10-01\n"
    "next_free: 10240\n"
    "bloky:\n"
    "  - rozsah: 10110-10159\n"
    "    vlastník: NEX Automat\n"
    "    druh: externý\n"
    "\n"
    "# porty mimo rozsahu\n"
    "mimo_rozsahu:\n"
    "  - 9216\n"
)
OLD_BLOCK = "\n  - rozsah: 10200-10209\n    vlastník: dedo-home\n    druh: kokpit\n"
SCHEMA = "# Career Asistent — Database Schemas\n\n## users\n\n| stĺpec | typ |\n|---|---|\n| id | uuid |\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def kb(tmp_path: Path, monkeypatch) -> Path:
    """A Knowledge Base in sync with its GitHub (a bare repository), with a file somebody is editing."""
    github = tmp_path / "github.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(github))
    root = tmp_path / "knowledge"
    for rel, text in {
        "icc/SCHEMA_GOVERNANCE.md": GOVERNANCE,
        "projects/INDEX.md": INDEX,
        "projects/demo/PROJECT.md": "# Demo\n",
        "infrastructure/port-registry.yaml": REGISTRY,
        "notes/somebodys.md": "rozpísané\n",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main", str(root))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "remote", "add", "origin", str(github))
    _git(root, "push", "-q", "origin", "main")
    (root / "notes/somebodys.md").write_text("rozpísané — ešte nie je hotové\n", encoding="utf-8")
    monkeypatch.setattr(port_registry, "PORT_REGISTRY_FILE", root / "infrastructure/port-registry.yaml")
    # The whole base, not just the registry in it: whoever writes it asks who owns the base (DEV-144).
    monkeypatch.setattr(settings, "knowledge_base_path", str(root))
    return root


def _github(kb: Path) -> Path:
    return kb.parent / "github.git"


def _github_head(kb: Path) -> str:
    return _git(_github(kb), "rev-parse", "main").strip()


def _head(kb: Path) -> str:
    return _git(kb, "rev-parse", "HEAD").strip()


def _publish_schema(kb: Path):
    return KnowledgeBaseSchemaPublisher(kb).publish(
        slug="career-asistent",
        project_name="Career Asistent",
        description="Pomocník pri hľadaní práce.",
        source_path="/opt/projects/career-asistent",
        content=SCHEMA,
        version_number="0.1.0",
        approved_by="alex",
    )


def _allocate(slug: str = "career-asistent", base: int = 10250) -> str | None:
    return port_registry.record_allocation(
        slug=slug, base=base, block_size=10, backend_port=base, frontend_port=base + 1, db_port=base + 2
    )


# ── the approved schema ───────────────────────────────────────────────────────────────────────────────────────────


def test_an_approved_schema_reaches_github(kb):
    result = _publish_schema(kb)

    assert result.commit and _github_head(kb) == result.commit == _head(kb)
    assert result.unpublished is None


def test_somebodys_unfinished_work_never_travels(kb):
    (kb / "notes/untracked.md").write_text("nový rozpísaný súbor\n", encoding="utf-8")

    _publish_schema(kb)

    on_github = _git(_github(kb), "show", "main:notes/somebodys.md")
    assert on_github == "rozpísané\n", "somebody's edit went to GitHub"
    assert "notes/untracked.md" not in _git(_github(kb), "ls-tree", "-r", "--name-only", "main")
    assert (kb / "notes/somebodys.md").read_text(encoding="utf-8") == "rozpísané — ešte nie je hotové\n"
    assert "?? notes/untracked.md" in _git(kb, "status", "--porcelain")


def test_somebodys_staged_work_stays_staged_and_out_of_the_commit(kb):
    (kb / "notes/somebodys.md").write_text("pripravené na commit\n", encoding="utf-8")
    _git(kb, "add", "notes/somebodys.md")

    result = _publish_schema(kb)

    assert "notes/somebodys.md" not in _git(kb, "show", "--name-only", "--format=", result.commit).split()
    assert _git(kb, "diff", "--cached", "--name-only").split() == ["notes/somebodys.md"]


def test_github_ahead_is_said_and_never_merged(kb, tmp_path):
    other = tmp_path / "elsewhere"
    _git(tmp_path, "clone", "-q", str(_github(kb)), str(other))
    (other / "projects/INDEX.md").write_text(INDEX + "| **iny** | `/y` | z |\n", encoding="utf-8")
    _git(other, "commit", "-qam", "from elsewhere")
    _git(other, "push", "-q", "origin", "main")
    theirs = _git(other, "rev-parse", "HEAD").strip()

    result = _publish_schema(kb)

    assert result.commit and _head(kb) == result.commit
    assert _github_head(kb) == theirs, "the cockpit overwrote what GitHub had"
    assert len(_git(kb, "log", "-1", "--format=%P").split()) == 1, "the cockpit merged in the shared tree"
    assert "GitHub" in result.unpublished and "nezlučuje" in result.unpublished


def test_an_unreachable_github_keeps_the_commit_and_the_next_publish_sends_it(kb, tmp_path):
    _git(kb, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))

    result = _publish_schema(kb)

    assert result.commit == _head(kb) and _github_head(kb) != result.commit
    assert result.unpublished and "GitHub" in result.unpublished

    _git(kb, "remote", "set-url", "origin", str(_github(kb)))
    assert kb_git.publish(kb) is None
    assert _github_head(kb) == result.commit


def _approve_schema(db_session, monkeypatch, tmp_path):
    """A design that changes the schema, waiting for Ri — the version and its Knowledge Base."""
    version, _, kb = _awaiting_navrh(db_session, monkeypatch, tmp_path, schema=APPROVED)
    return version, kb


def _note(db_session, version):
    [note] = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("database_schema_published")]
    return note


async def test_the_manager_is_told_when_the_approved_schema_stayed_on_andros(db_session, monkeypatch, tmp_path):
    version, kb = _approve_schema(db_session, monkeypatch, tmp_path)
    _git(kb, "remote", "add", "origin", str(tmp_path / "nowhere.git"))

    await orchestrator.apply_action(
        db_session, version_id=version.id, action="schvalit", payload={}, acting_user=_user(db_session, "ri")
    )

    note = _note(db_session, version)
    assert "Na GitHub sa zatiaľ nedostala" in note.content and "MAGER" in note.content
    unpublished = note.payload["database_schema_published"]["unpublished"]
    assert unpublished and unpublished in note.content


async def test_an_approved_schema_on_github_is_not_reported_as_stuck(db_session, monkeypatch, tmp_path):
    version, kb = _approve_schema(db_session, monkeypatch, tmp_path)
    github = tmp_path / "github.git"
    _git(tmp_path, "init", "-q", "--bare", str(github))
    _git(kb, "remote", "add", "origin", str(github))

    await orchestrator.apply_action(
        db_session, version_id=version.id, action="schvalit", payload={}, acting_user=_user(db_session, "ri")
    )

    note = _note(db_session, version)
    assert note.payload["database_schema_published"]["unpublished"] is None
    assert "GitHub" not in note.content
    branch = _git(kb, "symbolic-ref", "--short", "HEAD").strip()
    assert _git(github, "rev-parse", branch) == _git(kb, "rev-parse", "HEAD")


# ── a port block ──────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_port_block_is_committed_alone_and_reaches_github(kb):
    """A block written before this change lies uncommitted in the file; the new block is committed without it."""
    registry = kb / "infrastructure/port-registry.yaml"
    registry.write_text(REGISTRY.replace("\n\n# porty", OLD_BLOCK + "\n# porty"), encoding="utf-8")

    assert _allocate() is None

    committed = _git(kb, "show", "HEAD:infrastructure/port-registry.yaml")
    assert "rozsah: 10250-10259" in committed and "dedo-home" not in committed
    on_disk = registry.read_text(encoding="utf-8")
    assert "rozsah: 10250-10259" in on_disk and "dedo-home" in on_disk
    assert _git(kb, "show", "--name-only", "--format=", "HEAD").split() == ["infrastructure/port-registry.yaml"]
    assert _github_head(kb) == _head(kb)
    # staged = the commit; only the old block and somebody's note differ, in the working tree
    assert _git(kb, "status", "--porcelain").splitlines() == [
        " M infrastructure/port-registry.yaml",
        " M notes/somebodys.md",
    ]


def test_a_port_block_that_cannot_reach_github_is_said(kb, tmp_path):
    _git(kb, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
    before = _head(kb)

    warning = _allocate()

    assert _head(kb) != before, "the block was not committed"
    assert warning and "GitHub" in warning and "10250-10259" in warning


# ── a deleted project ─────────────────────────────────────────────────────────────────────────────────────────────


def test_a_deleted_projects_folder_is_committed_as_removed_and_reaches_github(kb):
    assert KnowledgeBaseWriter(kb).delete_project("demo") is True

    assert "projects/demo/PROJECT.md" not in _git(kb, "ls-tree", "-r", "--name-only", "HEAD")
    assert _git(kb, "show", "--name-only", "--format=", "HEAD").split() == ["projects/demo/PROJECT.md"]
    assert _github_head(kb) == _head(kb)
    assert _git(kb, "status", "--porcelain").splitlines() == [" M notes/somebodys.md"]


def test_a_staged_registry_is_never_committed_over(kb):
    """Somebody staged their own edit of the registry — the cockpit's commit would carry it or drop it."""
    registry = kb / "infrastructure/port-registry.yaml"
    registry.write_text(REGISTRY.replace("next_free: 10240", "next_free: 10300"), encoding="utf-8")
    _git(kb, "add", "infrastructure/port-registry.yaml")
    before = _head(kb)

    warning = _allocate()

    assert _head(kb) == before
    assert warning and "infrastructure/port-registry.yaml" in warning and "10250-10259" in warning
    assert _git(kb, "diff", "--cached", "--name-only").split() == ["infrastructure/port-registry.yaml"]


# ── the Knowledge Base page ───────────────────────────────────────────────────────────────────────────────────────


class _Indexer:
    async def index_document(self, **_kwargs):
        return {"chunks": 1}

    async def reindex_document(self, **_kwargs):
        return {"chunks": 1}

    async def delete_document(self, *_args, **_kwargs):
        return 1


@pytest.fixture
def page(kb, db_session, monkeypatch):
    """The Knowledge Base page's API, writing into ``kb`` as Ri."""
    monkeypatch.setattr(settings, "knowledge_base_path", str(kb))
    monkeypatch.setattr(knowledge_routes, "_get_indexer", lambda: _Indexer())
    return _build_client(db_session, _make_user(db_session, "ri"))


DOCUMENTS = "/api/v1/knowledge/documents"


def test_a_document_created_on_the_page_is_committed_alone_and_reaches_github(kb, page):
    created = page.post(DOCUMENTS, json={"category": "icc", "filename": "NOVY.md", "content": "# Nový postup\n"})

    assert created.status_code == 200, created.text
    assert created.json()["unpublished"] is None
    assert _git(kb, "show", "--name-only", "--format=", "HEAD").split() == ["icc/NOVY.md"]
    assert _git(kb, "show", "HEAD:icc/NOVY.md") == "# Nový postup\n"
    assert _github_head(kb) == _head(kb)
    assert _git(kb, "status", "--porcelain").splitlines() == [" M notes/somebodys.md"]


def test_a_document_saved_on_the_page_is_committed_alone_and_reaches_github(kb, page):
    saved = page.put(DOCUMENTS, json={"relative_path": "projects/demo/PROJECT.md", "content": "# Demo 2\n"})

    assert saved.status_code == 200, saved.text
    assert saved.json()["unpublished"] is None
    assert _git(kb, "show", "--name-only", "--format=", "HEAD").split() == ["projects/demo/PROJECT.md"]
    assert _git(kb, "show", "HEAD:projects/demo/PROJECT.md") == "# Demo 2\n"
    assert _github_head(kb) == _head(kb)
    assert _git(kb, "status", "--porcelain").splitlines() == [" M notes/somebodys.md"]


def test_a_document_deleted_on_the_page_is_committed_as_removed_and_reaches_github(kb, page):
    deleted = page.delete(DOCUMENTS, params={"relative_path": "projects/demo/PROJECT.md"})

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["unpublished"] is None
    assert _git(kb, "show", "--name-only", "--format=", "HEAD").split() == ["projects/demo/PROJECT.md"]
    assert "projects/demo/PROJECT.md" not in _git(kb, "ls-tree", "-r", "--name-only", "HEAD")
    assert _github_head(kb) == _head(kb)
    assert _git(kb, "status", "--porcelain").splitlines() == [" M notes/somebodys.md"]


def test_a_page_write_that_cannot_reach_github_says_why_and_keeps_the_commit(kb, page, tmp_path):
    _git(kb, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))

    saved = page.put(DOCUMENTS, json={"relative_path": "projects/demo/PROJECT.md", "content": "# Demo 2\n"})

    assert saved.status_code == 200, saved.text
    unpublished = saved.json()["unpublished"]
    assert unpublished.startswith("Odoslanie Znalostnej bázy na GitHub zlyhalo")
    assert unpublished.endswith("Kokpit to skúsi odoslať znova sám.")
    assert _git(kb, "show", "HEAD:projects/demo/PROJECT.md") == "# Demo 2\n"


def test_a_page_write_somebody_staged_is_not_committed_and_is_said(kb, page):
    (kb / "projects/demo/PROJECT.md").write_text("# Demo — rozpísané\n", encoding="utf-8")
    _git(kb, "add", "projects/demo/PROJECT.md")
    before = _head(kb)

    saved = page.put(DOCUMENTS, json={"relative_path": "projects/demo/PROJECT.md", "content": "# Demo 2\n"})

    assert saved.status_code == 200, saved.text
    assert _head(kb) == before
    assert saved.json()["unpublished"].startswith("Zápis sa neuložil do gitu Znalostnej bázy: V Znalostnej báze sú")


# ── what could not leave at once ──────────────────────────────────────────────────────────────────────────────────


async def test_the_loop_sends_again_and_says_why_once_not_every_pass(monkeypatch, caplog):
    reasons = iter(["GitHub neodpovedal.", "GitHub neodpovedal.", None, None])
    roots: list[Path] = []

    def _publish(root):
        roots.append(root)
        reason = next(reasons, "stop")
        if reason == "stop":
            raise asyncio.CancelledError
        return reason

    monkeypatch.setattr(kb_git, "publish", _publish)
    monkeypatch.setattr(settings, "kb_publish_interval_seconds", 0)
    monkeypatch.setattr(logging.getLogger("backend"), "propagate", True)

    with caplog.at_level(logging.INFO, logger="backend.main"), pytest.raises(asyncio.CancelledError):
        await main.kb_publish_loop()

    said = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "backend.main"]
    assert said == [
        (logging.WARNING, "Knowledge Base not on GitHub: GitHub neodpovedal."),
        (logging.INFO, "Knowledge Base on GitHub again"),
    ]
    assert len(roots) == 5 and set(roots) == {Path(settings.knowledge_base_path)}


def test_the_loop_runs_with_the_cockpit_and_not_in_tests():
    assert settings.kb_publish_enabled is False, "the suite would reach for GitHub"
    assert Settings.model_fields["kb_publish_enabled"].default is True


def test_no_test_reaches_the_live_knowledge_bases_git():
    with pytest.raises(pytest.fail.Exception, match="live Knowledge Base"):
        kb_git.git(Path(settings.knowledge_base_path), "status")


# ── the way to GitHub ─────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("git@github.com:rauschiccsk/icc-knowledge.git", "https://github.com/rauschiccsk/icc-knowledge.git"),
        ("ssh://git@github.com/rauschiccsk/icc-knowledge.git", "https://github.com/rauschiccsk/icc-knowledge.git"),
        ("https://github.com/rauschiccsk/icc-knowledge.git", "https://github.com/rauschiccsk/icc-knowledge.git"),
        ("/srv/git/knowledge.git", "/srv/git/knowledge.git"),
    ],
)
def test_github_is_reached_over_https_the_container_has_no_ssh(origin, expected):
    assert kb_git.https_url(origin) == expected


def test_the_token_never_appears_in_a_command_line(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "tajny-token-123")

    env = kb_git.network_env("https://github.com/rauschiccsk/icc-knowledge.git")

    settings_given = [v for k, v in env.items() if k.startswith("GIT_CONFIG_VALUE_")]
    assert settings_given and not any("tajny-token-123" in v for v in settings_given)
    assert "tajny-token-123" in env.values(), "the helper has nothing to answer GitHub with"
    assert env.get("GIT_TERMINAL_PROMPT") == "0"

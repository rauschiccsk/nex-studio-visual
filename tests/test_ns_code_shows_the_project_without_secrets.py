"""DEV-86 — NS Code shows a project's files read-only: what git knows or would add, never a secret, never a file from
outside the project; the tree, the file and the search decide by the same list.

Every test runs on a real git repository with the traps a real project has: an ignored ``node_modules``, an ``.env``
next to its template, a key, the manager's ``private/`` file, a symbolic link out and a hard link to a file outside.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

from backend.config.settings import settings
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.main import app
from backend.services import claude_agent, ns_code, secret_files

WORD = "faktúra-z-genesisu"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def _write(root: Path, rel: str, text: str = "") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or f"{rel}\n{WORD}\n", encoding="utf-8")
    return path


@pytest.fixture
def project_dir(tmp_path) -> Path:
    root = tmp_path / "projects" / "nex-inbox"
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "studio@isnex.eu")
    _git(root, "config", "user.name", "NEX Studio")
    _write(root, ".gitignore", "node_modules/\n")
    _write(root, "README.md")
    _write(root, "backend/main.py", f"def main():\n    return '{WORD}'\n")
    _write(root, "backend/.env.production")  # a secret somebody committed
    _write(root, ".env.example", "SMTP_PASSWORD=\n")
    _write(root, "certs/server.pem")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "start")
    _write(root, "new.py")  # untracked, not ignored — git would add it
    _write(root, ".env")  # untracked secret
    _write(root, "node_modules/lib/index.js")  # ignored
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "exclude").write_text("/private/\n")
    _write(root, "private/mail.eml")  # what the manager attached for the agent (DEV-44)
    _write(root, "private/forced.eml")  # … and one somebody forced into git past the exclude
    _git(root, "add", "-f", "private/forced.eml")
    outside = _write(tmp_path, "outside-secret.txt")
    os.symlink(outside, root / "link.txt")
    os.link(outside, root / "innocent.txt")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0" + WORD.encode())
    return root


def test_the_list_is_what_git_knows_or_would_add_without_secrets_links_or_outside_files(project_dir):
    assert ns_code.files(project_dir) == [
        ".env.example",
        ".gitignore",
        "README.md",
        "backend/main.py",
        "logo.png",
        "new.py",
    ]


def test_a_listed_file_is_served_as_text(project_dir):
    view = ns_code.read(project_dir, "backend/main.py")

    assert (view.text, view.binary, view.too_big) == (f"def main():\n    return '{WORD}'\n", False, False)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "backend/.env.production",
        "certs/server.pem",
        "node_modules/lib/index.js",
        "private/mail.eml",
        "private/forced.eml",
        "link.txt",
        "innocent.txt",
        "../outside-secret.txt",
        "backend/../../outside-secret.txt",
        "/etc/hostname",
        ".git/config",
    ],
)
def test_nothing_else_is_served(project_dir, path):
    with pytest.raises(ns_code.NotServed):
        ns_code.read(project_dir, path)


def test_a_binary_file_is_named_not_shown(project_dir):
    view = ns_code.read(project_dir, "logo.png")

    assert (view.binary, view.text) == (True, None)


def test_a_file_bigger_than_the_setting_is_named_not_loaded(project_dir, monkeypatch):
    monkeypatch.setattr(settings, "ns_code_max_file_bytes", 10)

    view = ns_code.read(project_dir, "README.md")

    assert (view.too_big, view.text, view.size) == (True, None, len(f"README.md\n{WORD}\n".encode()))


def test_the_search_finds_text_in_tracked_and_new_files_and_never_in_a_hidden_one(project_dir):
    found = ns_code.search(project_dir, WORD.upper())

    assert sorted({h.path for h in found.hits}) == ["README.md", "backend/main.py", "new.py"]
    hit = next(h for h in found.hits if h.path == "backend/main.py")
    assert (hit.line, hit.text) == (2, f"    return '{WORD}'")
    assert not found.truncated


def test_the_search_stops_at_the_setting_and_says_so(project_dir, monkeypatch):
    monkeypatch.setattr(settings, "ns_code_search_limit", 2)

    found = ns_code.search(project_dir, WORD)

    assert (len(found.hits), found.truncated) == (2, True)


def test_a_search_of_one_letter_is_refused(project_dir):
    with pytest.raises(ValueError):
        ns_code.search(project_dir, " a ")


def test_a_project_without_git_says_so(tmp_path):
    with pytest.raises(ns_code.NoCheckout):
        ns_code.files(tmp_path)


@pytest.mark.parametrize(
    ("name", "secret", "template"),
    [
        (".env", True, False),
        (".env.local", True, False),
        (".env.example", True, True),
        (".env.sample", True, True),
        ("server.key", True, False),
        ("id_ed25519", True, False),
        ("id_ed25519.pub", False, False),
        ("README.md", False, False),
    ],
)
def test_one_rule_says_what_a_secret_is(name, secret, template):
    assert (secret_files.is_secret_name(name), secret_files.is_template(name)) == (secret, template)


def test_poradca_reads_the_same_rule():
    """One place: Poradca's overlay and NS Code ask the same pattern, not two copies."""
    from backend.services.poradca import sandbox

    assert sandbox._SECRET_FILE_RE is secret_files.SECRET_FILE_RE


# ── the routes ─────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def served(db_session, project_dir, monkeypatch):
    db = db_session
    owner = User(
        username=f"o_{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@t.sk", password_hash="x", role="ri"
    )
    stranger = User(
        username=f"s_{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@t.sk", password_hash="x", role="ri"
    )
    db.add_all([owner, stranger])
    db.flush()
    project = Project(
        name="NEX Inbox",
        slug="nex-inbox",
        type="standard",
        auth_mode="password",
        description="",
        created_by=owner.id,
    )
    db.add(project)
    db.flush()
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", project_dir.parent)
    return db, owner, stranger


def _as(db, user):
    user_id = user.id
    app.dependency_overrides[get_current_user] = lambda: db.get(User, user_id)


def test_the_routes_serve_the_owner(client, served, project_dir):
    db, owner, _stranger = served
    _as(db, owner)

    listed = client.get("/api/v1/projects/nex-inbox/code/files").json()
    shown = client.get("/api/v1/projects/nex-inbox/code/file", params={"path": "backend/main.py"}).json()
    found = client.get("/api/v1/projects/nex-inbox/code/search", params={"q": WORD}).json()

    assert listed["files"] == ns_code.files(project_dir)
    assert listed["head"] == _git(project_dir, "rev-parse", "HEAD").strip()
    assert (shown["path"], shown["binary"], shown["max_bytes"]) == (
        "backend/main.py",
        False,
        settings.ns_code_max_file_bytes,
    )
    assert {h["path"] for h in found["hits"]} == {"README.md", "backend/main.py", "new.py"}


def test_the_route_refuses_a_secret_and_a_path_outside(client, served):
    db, owner, _stranger = served
    _as(db, owner)

    for path in (".env", "../outside-secret.txt"):
        answer = client.get("/api/v1/projects/nex-inbox/code/file", params={"path": path})
        assert (answer.status_code, answer.json()["detail"]) == (404, ns_code.NOT_SERVED)


def test_another_persons_project_shows_nothing(client, served):
    db, _owner, stranger = served
    _as(db, stranger)

    for url in ("/code/files", "/code/file?path=README.md", f"/code/search?q={WORD}"):
        assert client.get(f"/api/v1/projects/nex-inbox{url}").status_code in (403, 404)


def test_a_short_search_is_a_sentence_not_an_error_page(client, served):
    db, owner, _stranger = served
    _as(db, owner)

    answer = client.get("/api/v1/projects/nex-inbox/code/search", params={"q": "a"})

    assert (answer.status_code, answer.json()["detail"]) == (422, ns_code.SEARCH_TOO_SHORT)

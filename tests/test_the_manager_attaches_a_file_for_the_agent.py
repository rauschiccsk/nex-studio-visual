"""DEV-44 — the Manažér attaches a file for the AI Agent in the cockpit; it lands in ``private/``, git never sees it.

Career Asistent 0.1.0 stopped on 09.10.2026: task 1.2.1 needs a real e-mail from Profesia as an ``.eml`` file in
``/opt/projects/career-asistent/private/`` and the cockpit could not take a file, so Poradca advised ``scp`` over
Tailscale — a terminal step. Director: „Nahrať súbor má umožniť samotná aplikácia nex-studio-visual“, „Áno, založ
tiket do DEV a implementuj“.

Pinned here: the file is stored under a plain name in ``private/``, the repository's local exclude keeps it out of
git without touching a tracked file, a project whose rules pull the folder back into git is refused, the size is
capped, deletion stays inside the folder, the routes record who did what (never the content), only who may drive
the build may do it, Poradca sees the names but not the content, and both charters send everyone to the button.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.api.routes import project_files as files_route
from backend.config.settings import settings
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import get_db
from backend.services import claude_agent, orchestrator, project_files
from backend.services.pipeline_ws import registry
from backend.services.poradca import sandbox as poradca_sandbox

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
EML = b"From: agent@profesia.sk\r\nTo: zoltan@example.sk\r\nSubject: Nove ponuky\r\n\r\nhttps://www.profesia.sk/x\r\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30).stdout


def _repo(root: Path, gitignore: str = "node_modules/\n") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=30)
    (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    _git(root, "add", ".gitignore")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return root


# ── the service ──────────────────────────────────────────────────────────────────────────────────────────────


def test_the_file_lands_in_private_and_git_never_sees_it(tmp_path):
    root = _repo(tmp_path / "proj")

    stored = project_files.save(root, "profesia.eml", EML, max_bytes=1024)

    assert stored.path == "private/profesia.eml" and stored.size_bytes == len(EML)
    assert (root / "private" / "profesia.eml").read_bytes() == EML
    assert _git(root, "status", "--porcelain", "--untracked-files=all") == ""
    assert _git(root, "check-ignore", "private/profesia.eml").strip() == "private/profesia.eml"
    assert (root / ".gitignore").read_text(encoding="utf-8") == "node_modules/\n", "no tracked file changes"
    assert "/private/" in (root / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()


def test_a_second_file_of_the_same_name_does_not_overwrite_the_first(tmp_path):
    root = _repo(tmp_path / "proj")
    project_files.save(root, "profesia.eml", b"first", max_bytes=1024)

    second = project_files.save(root, "profesia.eml", b"second", max_bytes=1024)

    assert second.path == "private/profesia-2.eml"
    assert (root / "private" / "profesia.eml").read_bytes() == b"first"


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("Ponuka Profesie č. 2.eml", "Ponuka-Profesie-c.-2.eml"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\zoltan\\Desktop\\mail.eml", "mail.eml"),
        (".env", "env"),
        ("", "subor"),
        ("..", "subor"),
        ("a" * 200 + ".eml", "a" * 116 + ".eml"),
    ],
)
def test_the_name_becomes_a_plain_file_name_inside_private(given, stored):
    assert project_files.safe_name(given) == stored


def test_a_file_over_the_cap_is_refused_and_nothing_is_left_behind(tmp_path):
    root = _repo(tmp_path / "proj")

    with pytest.raises(project_files.PrivateFileRefused) as refused:
        project_files.save(root, "big.eml", b"x" * 2049, max_bytes=2048)

    assert str(refused.value) == "Súbor má viac než 2 kB, čo je najviac, čo sa dá priložiť — neuložil som ho."
    assert not (root / "private").exists()


def test_an_empty_file_is_refused(tmp_path):
    with pytest.raises(project_files.PrivateFileRefused, match="Súbor je prázdny"):
        project_files.save(_repo(tmp_path / "proj"), "x.eml", b"", max_bytes=1024)


def test_a_project_whose_rules_pull_private_back_into_git_is_refused_not_written(tmp_path):
    root = _repo(tmp_path / "proj", gitignore="!/private/\n!/private/**\n")

    with pytest.raises(project_files.PrivateFileRefused) as refused:
        project_files.save(root, "profesia.eml", EML, max_bytes=1024)

    assert "git by ho v projekte videl (private/profesia.eml)" in str(refused.value)
    assert not (root / "private" / "profesia.eml").exists()


def test_a_project_without_a_repository_has_nothing_to_commit_the_file_to(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()

    assert project_files.save(root, "x.eml", EML, max_bytes=1024).path == "private/x.eml"


def test_private_that_is_a_link_is_refused(tmp_path):
    root = _repo(tmp_path / "proj")
    (tmp_path / "elsewhere").mkdir()
    (root / "private").symlink_to(tmp_path / "elsewhere")

    with pytest.raises(project_files.PrivateFileRefused, match="niečo iné než priečinok"):
        project_files.save(root, "x.eml", EML, max_bytes=1024)
    assert list((tmp_path / "elsewhere").iterdir()) == []


def test_the_list_shows_what_the_agent_put_there_too_but_no_upload_in_flight_and_no_link(tmp_path):
    root = _repo(tmp_path / "proj")
    project_files.save(root, "profesia.eml", EML, max_bytes=1024)
    (root / "private" / "derived").mkdir()
    (root / "private" / "derived" / "notes.txt").write_text("agent", encoding="utf-8")
    (root / "private" / ".x.eml.abc.part").write_bytes(b"half")
    (root / "private" / "link.eml").symlink_to(root / ".gitignore")

    assert [f.path for f in project_files.list_files(root)] == ["private/derived/notes.txt", "private/profesia.eml"]


def test_delete_removes_a_file_inside_private_and_nothing_outside(tmp_path):
    root = _repo(tmp_path / "proj")
    project_files.save(root, "profesia.eml", EML, max_bytes=1024)
    (root / "secret.txt").write_text("keep", encoding="utf-8")
    (root / "private" / "link.txt").symlink_to(root / "secret.txt")

    assert project_files.delete(root, "private/profesia.eml").path == "private/profesia.eml"
    assert not (root / "private" / "profesia.eml").exists()
    for rel in ("private/../secret.txt", "secret.txt", "private/link.txt", "private/missing.eml", "private"):
        with pytest.raises(project_files.PrivateFileRefused, match="nie je čo zmazať"):
            project_files.delete(root, rel)
    assert (root / "secret.txt").read_text(encoding="utf-8") == "keep"


def test_the_line_for_the_answer_says_where_the_agent_finds_it():
    f = project_files.PrivateFile(path="private/profesia.eml", size_bytes=12 * 1024, modified_at=None)  # type: ignore[arg-type]

    assert project_files.answer_line(f) == (
        "Priložený súbor: `private/profesia.eml` (12 kB) — leží v koreni projektu v priečinku private/, mimo gitu."
    )
    assert [project_files.human_size(n) for n in (812, 2048, 1_468_006, 25 * 1024 * 1024)] == [
        "812 B",
        "2 kB",
        "1,4 MB",
        "25 MB",
    ]


# ── the routes ───────────────────────────────────────────────────────────────────────────────────────────────


def _user(db_session, role="ri") -> User:
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        password_hash=bcrypt.hashpw(b"test", bcrypt.gensalt(rounds=4)).decode(),
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _version(db_session, owner: User) -> tuple[Version, Path]:
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=owner.id,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number="0.1.0")
    db_session.add(version)
    db_session.flush()
    return version, _repo(claude_agent.PROJECTS_ROOT / project.slug)


@pytest.fixture()
def as_user(db_session, monkeypatch):
    """A client over the files routes, acting as the user it is given; broadcasts are collected."""
    sent: list[dict] = []

    async def _broadcast(version_id, frame):
        sent.append(frame)

    monkeypatch.setattr(registry, "broadcast", _broadcast)

    def _client(user: User) -> TestClient:
        app = FastAPI()
        app.include_router(files_route.router, prefix="/api/v1/pipeline")
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    _client.sent = sent  # type: ignore[attr-defined]
    return _client


def _messages(db_session, version_id) -> list[PipelineMessage]:
    return list(
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq)
        ).scalars()
    )


def test_an_attached_file_is_stored_recorded_and_handed_back_as_a_line_for_the_answer(db_session, as_user):
    owner = _user(db_session)
    version, root = _version(db_session, owner)
    client = as_user(owner)

    r = client.post(f"/api/v1/pipeline/{version.id}/files", files={"file": ("Profesia ponuky.eml", EML)})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file"]["path"] == "private/Profesia-ponuky.eml"
    assert body["file"]["uploaded_by"] == owner.username and body["file"]["uploaded_at"]
    assert body["answer_line"].startswith("Priložený súbor: `private/Profesia-ponuky.eml`")
    assert (root / "private" / "Profesia-ponuky.eml").read_bytes() == EML
    assert _git(root, "status", "--porcelain", "--untracked-files=all") == ""
    [msg] = _messages(db_session, version.id)
    assert msg.content == (
        f"{owner.username} priložil súbor private/Profesia-ponuky.eml ({len(EML)} B) — leží v projekte mimo gitu."
    )
    assert msg.payload["private_file"] == {
        "action": "uploaded",
        "path": "private/Profesia-ponuky.eml",
        "size_bytes": len(EML),
        "by": owner.username,
    }
    assert b"profesia.sk" not in str(msg.payload).encode() and "zoltan@" not in msg.content
    assert [f["type"] for f in as_user.sent] == ["message_added"]


def test_the_list_shows_who_attached_a_file_and_leaves_the_agents_own_files_unattributed(db_session, as_user):
    owner = _user(db_session)
    version, root = _version(db_session, owner)
    client = as_user(owner)
    client.post(f"/api/v1/pipeline/{version.id}/files", files={"file": ("profesia.eml", EML)})
    (root / "private" / "derived.txt").write_text("agent", encoding="utf-8")

    body = client.get(f"/api/v1/pipeline/{version.id}/files").json()

    assert [(f["path"], f["uploaded_by"]) for f in body["files"]] == [
        ("private/derived.txt", None),
        ("private/profesia.eml", owner.username),
    ]
    assert body["max_bytes"] == settings.private_file_max_bytes and body["max_label"] == "25 MB"


def test_delete_removes_the_file_and_records_who_did_it(db_session, as_user):
    owner = _user(db_session)
    version, root = _version(db_session, owner)
    client = as_user(owner)
    client.post(f"/api/v1/pipeline/{version.id}/files", files={"file": ("profesia.eml", EML)})

    r = client.delete(f"/api/v1/pipeline/{version.id}/files", params={"path": "private/profesia.eml"})

    assert r.status_code == 200 and r.json()["files"] == []
    assert not (root / "private" / "profesia.eml").exists()
    last = _messages(db_session, version.id)[-1]
    assert last.content == f"{owner.username} zmazal súbor private/profesia.eml z priečinka projektu."
    assert last.payload["private_file"]["action"] == "deleted"


def test_a_file_the_agent_writes_again_after_a_delete_is_not_credited_to_the_manager(db_session, as_user):
    owner = _user(db_session)
    version, root = _version(db_session, owner)
    client = as_user(owner)
    client.post(f"/api/v1/pipeline/{version.id}/files", files={"file": ("profesia.eml", EML)})
    client.delete(f"/api/v1/pipeline/{version.id}/files", params={"path": "private/profesia.eml"})
    (root / "private" / "profesia.eml").write_text("the agent's own", encoding="utf-8")

    body = client.get(f"/api/v1/pipeline/{version.id}/files").json()

    assert [(f["path"], f["uploaded_by"]) for f in body["files"]] == [("private/profesia.eml", None)]


def test_the_history_is_read_in_the_order_it_was_written_not_by_when_its_transaction_began(db_session, as_user):
    """``created_at`` of a message is the START of its transaction (PostgreSQL ``now()``), so a delete written
    after an upload can carry the earlier time — the full suite hit exactly that. ``seq`` is the write order."""
    owner = _user(db_session)
    version, root = _version(db_session, owner)
    (root / "private").mkdir()
    (root / "private" / "profesia.eml").write_text("the agent's own", encoding="utf-8")
    entry = {"path": "private/profesia.eml", "by": owner.username}
    for action, began in (("uploaded", "2026-10-09 18:52:00+00"), ("deleted", "2026-10-09 18:51:00+00")):
        msg = orchestrator._record_message(
            db_session,
            version_id=version.id,
            stage="programovanie",
            author="system",
            recipient="manazer",
            kind="notification",
            content=action,
            payload={"private_file": {**entry, "action": action}},
        )
        msg.created_at = began
        db_session.flush()

    body = as_user(owner).get(f"/api/v1/pipeline/{version.id}/files").json()

    assert [(f["path"], f["uploaded_by"]) for f in body["files"]] == [("private/profesia.eml", None)]


def test_a_refusal_reaches_the_screen_as_a_sentence(db_session, as_user, monkeypatch):
    monkeypatch.setattr(settings, "private_file_max_bytes", 16)
    owner = _user(db_session)
    version, root = _version(db_session, owner)

    r = as_user(owner).post(f"/api/v1/pipeline/{version.id}/files", files={"file": ("big.eml", EML)})

    assert r.status_code == 409
    assert r.json()["detail"] == "Súbor má viac než 16 B, čo je najviac, čo sa dá priložiť — neuložil som ho."
    assert not (root / "private").exists() and _messages(db_session, version.id) == []


def test_someone_who_may_not_drive_the_build_can_neither_attach_nor_see(db_session, as_user):
    owner = _user(db_session)
    stranger = _user(db_session, role="shu")
    version, root = _version(db_session, owner)
    client = as_user(stranger)

    assert client.post(f"/api/v1/pipeline/{version.id}/files", files={"file": ("x.eml", EML)}).status_code in (403, 404)
    assert client.get(f"/api/v1/pipeline/{version.id}/files").status_code in (403, 404)
    assert not (root / "private").exists()


# ── Poradca and the charters ─────────────────────────────────────────────────────────────────────────────────


def test_poradca_sees_the_names_in_private_but_not_the_content(tmp_path):
    root = tmp_path / "proj"
    (root / "private" / "derived").mkdir(parents=True)
    (root / "private" / "profesia.eml").write_bytes(EML)
    (root / "private" / "derived" / "notes.txt").write_text("x", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "private_api.py").write_text("x", encoding="utf-8")

    overlays = poradca_sandbox.overlay_paths(str(root))

    assert overlays == [os.path.join("private", "derived", "notes.txt"), os.path.join("private", "profesia.eml")]


def _flat(name: str) -> str:
    return " ".join((TEMPLATES / name).read_text(encoding="utf-8").split())


def test_the_agent_is_told_to_ask_for_the_button_never_for_a_terminal():
    charter = _flat("ai-agent-charter.md")

    assert "nech ho priloží tlačidlom „Priložiť súbor“ v Riadiacom centre" in charter
    assert "Nežiadaj `scp`, terminál ani ukladanie na server a nepýtaj obsah súboru do textu správy" in charter
    assert "Originál do gitu nedávaj" in charter


def test_poradca_advises_the_button_and_knows_it_cannot_read_the_files():
    charter = _flat("poradca-charter.md")

    assert "priloží Manažér v Riadiacom centre tlačidlom „Priložiť súbor“" in charter
    assert "Nikdy neraď `scp`, terminál ani cestu na serveri" in charter
    assert "Obsah súborov v `private/` nevidíš — vidíš len ich mená" in charter

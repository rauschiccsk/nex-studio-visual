"""DEV-52 — a screenshot pasted into a question for Poradca reaches Poradca, and nobody else.

Director 10.10.2026: „Do editoru pre Poradcu chcem zabudovať možnosť priania screenshotu podobne ako je to možné
v Claude Desktop.“ The images travel with the question; the backend lets in only images (by content), within the
limits in ``settings``; they lie next to the conversation's record in Poradca's data, are mounted read-only into
that conversation's container (where restricted Claude Code may read them via ``--add-dir`` — probed 10.10.2026:
read with it, refused without it) and go when the conversation is deleted.
"""

from __future__ import annotations

import base64
import io
import math
import re
import uuid
from pathlib import Path

import pytest
from PIL import Image

from backend.api.routes import poradca as poradca_routes
from backend.config.settings import settings
from backend.db.models.poradca import PoradcaConversation, PoradcaMessage
from backend.schemas.poradca import PoradcaStatus
from backend.services.poradca import attachments, runner, sandbox
from tests.test_poradca_api import _client, _project, _user
from tests.test_poradca_sandbox import project  # noqa: F401 — the fixture: a demo project, Poradca's data in tmp

REPO = Path(__file__).resolve().parents[1]


def _png(color=(20, 60, 160), size=(40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@pytest.fixture
def poradca(monkeypatch, tmp_path):
    """Poradca's data in a temporary folder; the question is recorded by the REAL ``runner.ask`` and its answer
    (the container) replaced by a recorder of what the answer was asked."""
    monkeypatch.setattr(sandbox.settings, "poradca_data_dir", str(tmp_path))
    asked: list[str] = []

    async def _no_container(message_id, conversation_id, question, user_id, first):
        asked.append(question)
        runner._running.pop(message_id, None)

    monkeypatch.setattr(runner, "_run", _no_container)
    monkeypatch.setattr(
        poradca_routes.readiness,
        "status",
        lambda db: PoradcaStatus(ready=True, problems=[], running=0, max_concurrent=3),
    )
    return asked


def _start(c, proj, question="Čo je na snímke?", images=()):
    return c.post(
        f"/api/v1/poradca/projects/{proj.slug}/conversations",
        json={"question": question, "attachments": [{"name": n, "data": _b64(d)} for n, d in images]},
    )


# ── what is let in ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (_png(), "image/png"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 20, "image/jpeg"),
        (b"GIF89a" + b"\x00" * 20, "image/gif"),
        (b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 20, "image/webp"),
        (b"%PDF-1.7 obsah", None),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", None),
    ],
)
def test_the_type_is_read_from_the_content(data, mime):
    assert attachments.detect(data) == mime


def test_a_renamed_file_is_not_an_image():
    with pytest.raises(attachments.AttachmentRefused, match="nie je obrázok PNG, JPEG, WebP ani GIF"):
        attachments.accept([("snimka.png", _b64(b"toto je text, nie obrazok"))])


def test_too_many_too_large_or_broken_images_are_refused(monkeypatch):
    monkeypatch.setattr(settings, "poradca_attachments_max_count", 2)
    with pytest.raises(attachments.AttachmentRefused, match="najviac 2 obrázkov"):
        attachments.accept([("a.png", _b64(_png()))] * 3)

    monkeypatch.setattr(settings, "poradca_attachment_max_bytes", 50)
    with pytest.raises(attachments.AttachmentRefused, match="jeden obrázok môže mať najviac"):
        attachments.accept([("a.png", _b64(_png()))])

    monkeypatch.setattr(settings, "poradca_attachment_max_bytes", 10_000)
    monkeypatch.setattr(settings, "poradca_attachments_max_total_bytes", len(_png()) + 10)
    with pytest.raises(attachments.AttachmentRefused, match="spolu najviac"):
        attachments.accept([("a.png", _b64(_png())), ("b.png", _b64(_png()))])

    with pytest.raises(attachments.AttachmentRefused, match="neprišiel celý"):
        attachments.accept([("a.png", "toto nie je base64!")])
    # A stray character a lenient decoder would silently drop — the image did not arrive as it was sent.
    damaged = _b64(_png())[:8] + "!" + _b64(_png())[8:]
    with pytest.raises(attachments.AttachmentRefused, match="neprišiel celý"):
        attachments.accept([("a.png", damaged)])


def test_a_stored_image_is_found_only_by_its_own_id(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox.settings, "poradca_data_dir", str(tmp_path))
    cid = uuid.uuid4()
    [meta] = attachments.store(cid, attachments.accept([("../../etc/passwd.png", _b64(_png()))]))

    path = attachments.path_of(cid, meta)
    assert path.parent == sandbox.attachments_dir(cid) and path.read_bytes() == _png()
    assert path.stat().st_mode & 0o777 == 0o444, "snímku nesmie nikto prepísať"
    assert meta["name"] == "../../etc/passwd.png", "meno je len popis — súbor sa volá podľa id"
    assert attachments.path_of(cid, {**meta, "id": "../" + meta["id"][3:]}) is None
    assert attachments.path_of(cid, {**meta, "mime": "text/html"}) is None


# ── the question carries them ─────────────────────────────────────────────────


def test_a_question_with_a_screenshot_records_it_and_tells_poradca_where_it_is(db_session, poradca):
    owner = _user(db_session)
    proj = _project(db_session, owner)
    c = _client(db_session, owner)

    created = _start(c, proj, images=[("Snímka 2026-10-10.png", _png())])

    assert created.status_code == 201, created.text
    cid = uuid.UUID(created.json()["id"])
    [human] = [m for m in created.json()["messages"] if m["author"] == "human"]
    [meta] = human["attachments"]
    assert (meta["name"], meta["mime"], meta["size_bytes"]) == ("Snímka 2026-10-10.png", "image/png", len(_png()))
    assert attachments.path_of(cid, meta).read_bytes() == _png()
    assert human["content"] == "Čo je na snímke?", "poznámka pre Poradcu nie je text otázky"
    [asked] = poradca
    assert asked.startswith("Čo je na snímke?\n\n[Manažér k otázke priložil snímky obrazovky.")
    assert f"{sandbox.CONTAINER_ATTACHMENTS_DIR}/{meta['id']}.png („Snímka 2026-10-10.png“)" in asked


def test_a_refused_image_creates_no_conversation(db_session, poradca):
    owner = _user(db_session)
    proj = _project(db_session, owner)
    c = _client(db_session, owner)

    refused = _start(c, proj, images=[("faktura.png", b"%PDF-1.7 obsah")])

    assert refused.status_code == 422 and "nie je obrázok" in refused.json()["detail"]
    assert db_session.query(PoradcaConversation).filter(PoradcaConversation.project_id == proj.id).count() == 0
    assert poradca == []


def test_the_image_is_given_only_to_whoever_may_read_the_conversation(db_session, poradca):
    owner = _user(db_session)
    proj = _project(db_session, owner)
    c = _client(db_session, owner)
    first = _start(c, proj, images=[("a.png", _png())]).json()
    other = _start(c, proj, "Iný rozhovor", images=[("b.png", _png((200, 0, 0)))]).json()
    meta = [m for m in first["messages"] if m["author"] == "human"][0]["attachments"][0]
    url = f"/api/v1/poradca/conversations/{first['id']}/attachments/{meta['id']}"

    mine = c.get(url)
    assert (mine.status_code, mine.headers["content-type"], mine.content) == (200, "image/png", _png())
    stranger = _client(db_session, _user(db_session))
    assert stranger.get(url).status_code == 404, "cudzí rozhovor"
    assert c.get(f"/api/v1/poradca/conversations/{other['id']}/attachments/{meta['id']}").status_code == 404
    assert c.get(f"/api/v1/poradca/conversations/{first['id']}/attachments/..%2F..%2Fempty").status_code == 404


def test_deleting_the_conversation_deletes_its_images(db_session, poradca):
    owner = _user(db_session)
    proj = _project(db_session, owner)
    c = _client(db_session, owner)
    cid = _start(c, proj, images=[("a.png", _png())]).json()["id"]
    folder = sandbox.attachments_dir(uuid.UUID(cid))
    assert any(folder.iterdir())
    # The answer is not run here (no container) — close it, as a finished answer would be.
    db_session.query(PoradcaMessage).filter(PoradcaMessage.conversation_id == uuid.UUID(cid)).update({"status": "done"})
    db_session.commit()

    assert c.delete(f"/api/v1/poradca/conversations/{cid}").status_code == 204

    assert not folder.exists() and not any(sandbox.trash_dir().iterdir())
    db_session.expire_all()
    assert all(
        m.attachments == []
        for m in db_session.query(PoradcaMessage).filter(PoradcaMessage.conversation_id == uuid.UUID(cid))
    )


# ── the container sees them, and only its own ────────────────────────────────


def _argv(cid, attached: bool) -> list[str]:
    return sandbox.run_argv(
        project_slug="demo",
        conversation_id=cid,
        token="t",
        network=None,
        call=sandbox.ClaudeCall(
            prompt="otázka",
            claude_session_id=uuid.uuid4(),
            charter_text=None,
            model=None,
            effort=None,
            attachments=attached,
        ),
        overlays=[],
    )


def test_the_container_reads_its_own_conversations_images_and_nothing_more(project):  # noqa: F811
    cid = uuid.uuid4()

    argv = _argv(cid, attached=True)

    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
    attached = [m for m in mounts if sandbox.CONTAINER_ATTACHMENTS_DIR in m]
    assert attached == [
        f"type=bind,source={sandbox.attachments_dir(cid)},target={sandbox.CONTAINER_ATTACHMENTS_DIR},readonly"
    ]
    at = argv.index("--add-dir")
    assert argv[at + 1] == sandbox.CONTAINER_ATTACHMENTS_DIR and argv[at + 2].startswith("--"), (
        "--add-dir berie viac priečinkov — za ním musí byť prepínač, inak pohltí text otázky"
    )
    assert argv[-1] == "otázka"

    plain = _argv(cid, attached=False)
    assert "--add-dir" not in plain and not any(sandbox.CONTAINER_ATTACHMENTS_DIR in a for a in plain)


def test_poradcas_step_names_the_screenshot_not_a_container_path():
    assert (
        runner._builtin_target("Read", {"file_path": f"{sandbox.CONTAINER_ATTACHMENTS_DIR}/ab.png"}, "/opt/projects/x")
        == "priložená snímka obrazovky"
    )


def test_trash_takes_the_record_and_the_images_together_and_gives_both_back(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox.settings, "poradca_data_dir", str(tmp_path))
    cid = uuid.uuid4()
    (sandbox.session_dir(cid) / "s.jsonl").parent.mkdir(parents=True)
    (sandbox.session_dir(cid) / "s.jsonl").write_text("záznam")
    sandbox.attachments_dir(cid).mkdir(parents=True)
    (sandbox.attachments_dir(cid) / "a.png").write_bytes(b"png")

    trashed = sandbox.move_to_trash(cid)
    assert not sandbox.session_dir(cid).exists() and not sandbox.attachments_dir(cid).exists()
    sandbox.restore_from_trash(trashed, cid)
    assert (sandbox.session_dir(cid) / "s.jsonl").read_text() == "záznam"
    assert (sandbox.attachments_dir(cid) / "a.png").read_bytes() == b"png" and not trashed.exists()

    # The images cannot be moved → the record goes back too: the conversation is never left half-deleted.
    real = sandbox.os.rename

    def _second_fails(src, dst):
        if "attachments" in str(src):
            raise OSError("disk")
        return real(src, dst)

    with monkeypatch.context() as m:
        m.setattr(sandbox.os, "rename", _second_fails)
        with pytest.raises(OSError):
            sandbox.move_to_trash(cid)
    assert (sandbox.session_dir(cid) / "s.jsonl").exists() and (sandbox.attachments_dir(cid) / "a.png").exists()
    assert not any(sandbox.trash_dir().iterdir())


# ── the cockpit's nginx lets the largest request through ─────────────────────


def test_the_cockpit_nginx_lets_through_every_upload_the_backend_accepts():
    """Without ``client_max_body_size`` nginx refused anything over 1 MB with 413 before the backend saw it
    (measured 10.10.2026: 2 MB → 413, 0.5 MB → 401) — a screenshot and a file for the agent alike."""
    text = (REPO / "frontend" / "nginx.conf").read_text()
    found = re.findall(r"client_max_body_size\s+(\d+)([kKmM]?)\s*;", text)
    assert len(found) == 1, found
    number, unit = found[0]
    allowed = int(number) * {"": 1, "k": 1024, "m": 1024 * 1024}[unit.lower()]
    largest = max(
        settings.private_file_max_bytes,
        math.ceil(settings.poradca_attachments_max_total_bytes * 4 / 3) + 64 * 1024,  # base64 + the question
    )
    assert allowed >= largest, f"nginx pustí {allowed} B, backend prijme až {largest} B"

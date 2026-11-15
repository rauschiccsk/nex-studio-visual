"""DEV-389 — a document attached in Nápady or Poradca reaches the agent whole.

15.11.2026 the Director wanted to give Projektant the design of Orthodox Hermenia (customer-requirements.md, 35 254
characters): the attach button refused it („nie je PNG, JPEG, WebP ani GIF“) and pasted as text it was longer than a
question may be (20 000 characters). Director: „Súhlasím, založ tiket a aj urob.“

A document — Markdown, plain text, PDF — goes the way a screenshot goes: recognised by its content, stored beside the
conversation, mounted read-only into the agent's container, and the question tells the agent to read it whole. The
question's length does not cut it.
"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path

import pytest

from backend.schemas.poradca import MAX_QUESTION_CHARS
from backend.services.poradca import attachments, readiness, runner, sandbox
from tests.test_poradca_api import _client, _project, _user
from tests.test_poradca_sees_pasted_screenshots import _png, _start, poradca  # noqa: F401 — the fixture by name

#: A design as long as Orthodox Hermenia's, in Markdown with Slovak letters — longer than a question may be.
DESIGN = ("# Orthodox Hermenia — návrh aplikácie\n\n" + "Ikonopisec hľadá predlohu podľa sviatku a svätého. " * 700)[
    :35_254
]
WORD = b"PK\x03\x04\x14\x00\x06\x00\x08\x00\x00\x00!\x00word/document.xml"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_the_design_is_longer_than_a_question_may_be():
    assert len(DESIGN) == 35_254 > MAX_QUESTION_CHARS


@pytest.mark.parametrize(
    ("name", "data", "mime"),
    [
        ("customer-requirements.md", DESIGN.encode(), "text/markdown"),
        ("poznamky.txt", "Pravoslávne sviatky\r\n".encode(), "text/plain"),
        ("navrh.pdf", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj", "application/pdf"),
        ("navrh.docx", WORD, None),
        ("stary.txt", "Pravoslávne sviatky".encode("cp1250"), None),  # not UTF-8: it would reach the agent garbled
        ("prazdny.md", b"   \n", None),
    ],
)
def test_a_document_is_known_by_its_content(name, data, mime):
    assert attachments.detect(data, name) == mime


def test_word_is_refused_with_what_the_cockpit_takes():
    with pytest.raises(attachments.AttachmentRefused) as refused:
        attachments.accept([("navrh.docx", _b64(WORD))])

    assert str(refused.value) == (
        "„navrh.docx“ nie je obrázok (PNG, JPEG, WebP, GIF) ani dokument (Markdown, text v UTF-8, PDF)."
    )


def test_a_new_idea_with_the_design_tells_projektant_to_read_it_whole(db_session, poradca):  # noqa: F811
    me = _user(db_session)
    c = _client(db_session, me)

    created = c.post(
        "/api/v1/poradca/ideas",
        json={
            "question": "Nový projekt Orthodox Hermenia — návrh je v prílohe.",
            "attachments": [{"name": "customer-requirements.md", "data": _b64(DESIGN.encode())}],
        },
    )

    assert created.status_code == 201, created.text
    conversation_id = uuid.UUID(created.json()["conversation_id"])
    [human] = [m for m in created.json()["conversation"]["messages"] if m["author"] == "human"]
    [meta] = human["attachments"]
    assert (meta["name"], meta["mime"]) == ("customer-requirements.md", "text/markdown")
    assert attachments.path_of(conversation_id, meta).read_text(encoding="utf-8") == DESIGN
    [asked] = poradca
    assert asked == (
        "Nový projekt Orthodox Hermenia — návrh je v prílohe.\n\n"
        "[Manažér k otázke priložil dokumenty — sú súčasťou jeho správy. Skôr než odpovieš, prečítaj každý CELÝ "
        "nástrojom Read (dlhý text po častiach, PDF po stranách) a vychádzaj z neho; sú len na čítanie:\n"
        f"- {sandbox.CONTAINER_ATTACHMENTS_DIR}/{meta['id']}.md („customer-requirements.md“)]"
    )


def test_poradca_gets_a_screenshot_and_a_pdf_each_with_its_own_instruction(db_session, poradca):  # noqa: F811
    owner = _user(db_session)
    c = _client(db_session, owner)

    created = _start(
        c, _project(db_session, owner), "Sedí obrazovka s návrhom?", images=[("s.png", _png()), ("n.pdf", b"%PDF-1.7")]
    )

    assert created.status_code == 201, created.text
    [asked] = poradca
    assert "[Manažér k otázke priložil snímky obrazovky." in asked and ".png („s.png“)" in asked
    assert "[Manažér k otázke priložil dokumenty" in asked and ".pdf („n.pdf“)" in asked


def test_a_text_document_comes_back_as_plain_text_never_as_a_page(db_session, poradca):  # noqa: F811
    owner = _user(db_session)
    c = _client(db_session, owner)
    page = b"<html><script>alert(1)</script></html>"
    design = DESIGN.encode()
    files = [("stranka.html", page), ("navrh.md", design)]
    first = _start(c, _project(db_session, owner), "Pozri", images=files).json()
    page_meta, design_meta = [m for m in first["messages"] if m["author"] == "human"][0]["attachments"]

    url = f"/api/v1/poradca/conversations/{first['id']}/attachments"
    got = [c.get(f"{url}/{m['id']}") for m in (page_meta, design_meta)]

    assert [(g.status_code, g.headers["content-type"], g.content) for g in got] == [
        (200, "text/plain; charset=utf-8", page),
        (200, "text/plain; charset=utf-8", design),  # Markdown too: a browser shows plain text, never asks to save
    ]


def test_the_agents_step_names_a_document_as_a_document():
    target = runner._builtin_target(
        "Read", {"file_path": f"{sandbox.CONTAINER_ATTACHMENTS_DIR}/ab.md"}, "/opt/projects/orthodox-hermenia"
    )
    assert target == "priložený dokument"


def test_the_screen_is_told_every_type_and_the_names_a_document_is_picked_by(db_session, monkeypatch):
    monkeypatch.setattr(readiness, "problems", lambda: [])

    status = readiness.status(db_session)

    assert status.attachment_types == [
        "application/pdf",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
        "text/markdown",
        "text/plain",
    ]
    assert status.attachment_extensions == [".md", ".markdown", ".txt", ".pdf"]


def test_the_agents_container_can_render_a_longer_pdf():
    """Claude Code renders the pages of a longer PDF with pdftoppm; the agents' container runs on the backend image
    (``build_sandbox.sandbox_image``) — its one recipe installs poppler-utils and the build fails without pdftoppm."""
    recipe = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "poppler-utils" in recipe
    assert 'pdftoppm_bin="$(command -v pdftoppm)" && test -x "$pdftoppm_bin"' in recipe

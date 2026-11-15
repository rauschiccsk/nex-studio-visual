"""DEV-52 — screenshots the Manažér pastes into a question for Poradca; DEV-389 — documents he attaches.

Director 10.10.2026: „Do editoru pre Poradcu chcem zabudovať možnosť priania screenshotu podobne ako je to možné
v Claude Desktop.“ The screen sends the images with the question (base64, like the Claude API itself); this module
decides what is let in and where it lies:

* only images — PNG, JPEG, WebP, GIF — recognised by their CONTENT, never by the name (a renamed file is refused);
* within the limits from ``settings`` (one image, how many, all of one question together);
* stored next to the conversation's record in Poradca's data (:func:`sandbox.attachments_dir`) — never in the
  project, never in git; deleting the conversation deletes them (:func:`sandbox.move_to_trash`);
* Poradca opens them with Read from its container, where they are mounted read-only (:mod:`.sandbox`).

DEV-389 — 15.11.2026 the Director wanted to give Projektant the design of Orthodox Hermenia (customer-requirements.md,
35 254 characters): the attach button took only images and the text was longer than a question may be (20 000). A
document — Markdown, plain text, PDF — now goes the same way as a screenshot and the agent reads it whole with Read,
so the length of a question does not cut it. Its type is the content's too: PDF by its signature, text when it is
UTF-8 without NUL bytes (a Word file or a renamed binary is neither); only Markdown vs plain text comes from the name.
"""

from __future__ import annotations

import base64
import binascii
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from backend.config.settings import settings
from backend.services.poradca import sandbox

#: The image types Poradca accepts, by MIME type → the extension the stored file gets.
IMAGE_TYPES: dict[str, str] = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
#: DEV-389 — the document types, the same way.
DOCUMENT_TYPES: dict[str, str] = {"text/markdown": "md", "text/plain": "txt", "application/pdf": "pdf"}
TYPES: dict[str, str] = {**IMAGE_TYPES, **DOCUMENT_TYPES}
#: The names a document is picked by on the screen (the browser often gives a text file no type) — the content still
#: decides; a name ending so marks the text as Markdown.
DOCUMENT_EXTENSIONS: tuple[str, ...] = (".md", ".markdown", ".txt", ".pdf")
_MARKDOWN_EXTENSIONS = (".md", ".markdown")
#: What a refusal names as accepted.
ACCEPTED_WORDS = "obrázok (PNG, JPEG, WebP, GIF) ani dokument (Markdown, text v UTF-8, PDF)"

#: The longest name of a pasted file kept for the screen.
NAME_MAX_CHARS = 200


class AttachmentRefused(ValueError):
    """The attachments cannot be accepted — the message says why, for the Manažér."""


@dataclass(frozen=True)
class Incoming:
    name: str
    data: bytes
    mime: str


def detect(data: bytes, name: str = "") -> Optional[str]:
    """The type by the content — an image or a PDF by its first bytes (its signature), text when the whole file is
    UTF-8 without NUL bytes (Markdown when ``name`` ends .md); ``None`` for anything else."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    image = _image_type(data)
    if image is not None:
        return image
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text.strip() or "\x00" in text:
        return None
    return "text/markdown" if name.lower().endswith(_MARKDOWN_EXTENSIONS) else "text/plain"


def _image_type(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):.0f} MB"


def accept(items: Iterable[tuple[str, str]]) -> list[Incoming]:
    """Decode and check the attachments of one question — all of them, before anything is stored.

    Raises:
        AttachmentRefused: too many, too large, not base64, or neither an image nor a document of an accepted type.
    """
    items = list(items)
    if len(items) > settings.poradca_attachments_max_count:
        raise AttachmentRefused(
            f"K jednej otázke sa dá priložiť najviac {settings.poradca_attachments_max_count} súborov "
            f"(priložených je {len(items)})."
        )
    accepted: list[Incoming] = []
    total = 0
    for raw_name, encoded in items:
        name = " ".join((raw_name or "").split())[:NAME_MAX_CHARS] or "snímka"
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AttachmentRefused(f"Súbor „{name}“ neprišiel celý — skús ho priložiť znova.") from exc
        if len(data) > settings.poradca_attachment_max_bytes:
            raise AttachmentRefused(
                f"Súbor „{name}“ má {_mb(len(data))} — jeden súbor môže mať najviac "
                f"{_mb(settings.poradca_attachment_max_bytes)}."
            )
        mime = detect(data, name)
        if mime is None:
            raise AttachmentRefused(f"„{name}“ nie je {ACCEPTED_WORDS}.")
        total += len(data)
        if total > settings.poradca_attachments_max_total_bytes:
            raise AttachmentRefused(
                f"Prílohy jednej otázky môžu mať spolu najviac {_mb(settings.poradca_attachments_max_total_bytes)}."
            )
        accepted.append(Incoming(name=name, data=data, mime=mime))
    return accepted


def store(conversation_id: uuid.UUID, files: list[Incoming]) -> list[dict]:
    """Write the attachments into the conversation's folder; return what the question message records about them.

    Each file is written under a temporary name and renamed into place, so a half-written file never lies there.
    Readable by the container's user (it runs as 1000), writable by nobody.
    """
    if not files:
        return []
    folder = sandbox.attachments_dir(conversation_id)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o755)
    stored: list[dict] = []
    try:
        for one in files:
            attachment_id = uuid.uuid4().hex
            final = folder / f"{attachment_id}.{TYPES[one.mime]}"
            partial = folder / f".{attachment_id}.part"
            partial.write_bytes(one.data)
            os.chmod(partial, 0o444)
            os.rename(partial, final)
            stored.append({"id": attachment_id, "name": one.name, "mime": one.mime, "size_bytes": len(one.data)})
    except OSError:
        discard(conversation_id, stored)
        raise
    return stored


def discard(conversation_id: uuid.UUID, stored: list[dict]) -> None:
    """Remove attachments stored for a question that was not recorded after all."""
    for meta in stored:
        path = path_of(conversation_id, meta)
        if path is not None:
            path.unlink(missing_ok=True)


def path_of(conversation_id: uuid.UUID, meta: dict) -> Optional[Path]:
    """The file of one recorded attachment — only ever inside the conversation's own folder."""
    attachment_id, mime = str(meta.get("id", "")), meta.get("mime")
    if len(attachment_id) != 32 or any(c not in "0123456789abcdef" for c in attachment_id) or mime not in TYPES:
        return None
    return sandbox.attachments_dir(conversation_id) / f"{attachment_id}.{TYPES[mime]}"


def container_path(meta: dict) -> str:
    return f"{sandbox.CONTAINER_ATTACHMENTS_DIR}/{meta['id']}.{TYPES[meta['mime']]}"


def is_document(meta: dict) -> bool:
    return meta.get("mime") in DOCUMENT_TYPES


def prompt_note(stored: list[dict]) -> str:
    """What the question tells the agent about its attachments — where they are, and that it is to open each (a
    document whole: it carries what the question itself could not)."""
    if not stored:
        return ""
    images = [meta for meta in stored if not is_document(meta)]
    documents = [meta for meta in stored if is_document(meta)]
    parts = []
    if images:
        lines = "\n".join(f"- {container_path(meta)} („{meta['name']}“)" for meta in images)
        parts.append(
            "[Manažér k otázke priložil snímky obrazovky. Skôr než odpovieš, otvor každú nástrojom Read — "
            f"sú len na čítanie:\n{lines}]"
        )
    if documents:
        lines = "\n".join(f"- {container_path(meta)} („{meta['name']}“)" for meta in documents)
        parts.append(
            "[Manažér k otázke priložil dokumenty — sú súčasťou jeho správy. Skôr než odpovieš, prečítaj každý CELÝ "
            "nástrojom Read (dlhý text po častiach, PDF po stranách) a vychádzaj z neho; sú len na čítanie:\n"
            f"{lines}]"
        )
    return "\n\n" + "\n\n".join(parts)

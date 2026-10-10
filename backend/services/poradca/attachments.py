"""DEV-52 — screenshots the Manažér pastes into a question for Poradca.

Director 10.10.2026: „Do editoru pre Poradcu chcem zabudovať možnosť priania screenshotu podobne ako je to možné
v Claude Desktop.“ The screen sends the images with the question (base64, like the Claude API itself); this module
decides what is let in and where it lies:

* only images — PNG, JPEG, WebP, GIF — recognised by their CONTENT, never by the name (a renamed file is refused);
* within the limits from ``settings`` (one image, how many, all of one question together);
* stored next to the conversation's record in Poradca's data (:func:`sandbox.attachments_dir`) — never in the
  project, never in git; deleting the conversation deletes them (:func:`sandbox.move_to_trash`);
* Poradca opens them with Read from its container, where they are mounted read-only (:mod:`.sandbox`).
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

#: The longest name of a pasted file kept for the screen.
NAME_MAX_CHARS = 200


class AttachmentRefused(ValueError):
    """The images cannot be accepted — the message says why, for the Manažér."""


@dataclass(frozen=True)
class Incoming:
    name: str
    data: bytes
    mime: str


def detect(data: bytes) -> Optional[str]:
    """The image type by the file's first bytes (its signature) — ``None`` for anything else."""
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
    """Decode and check the images of one question — all of them, before anything is stored.

    Raises:
        AttachmentRefused: too many, too large, not base64, or not an image of an accepted type.
    """
    items = list(items)
    if len(items) > settings.poradca_attachments_max_count:
        raise AttachmentRefused(
            f"K jednej otázke sa dá priložiť najviac {settings.poradca_attachments_max_count} obrázkov "
            f"(priložených je {len(items)})."
        )
    accepted: list[Incoming] = []
    total = 0
    for raw_name, encoded in items:
        name = " ".join((raw_name or "").split())[:NAME_MAX_CHARS] or "snímka"
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AttachmentRefused(f"Obrázok „{name}“ neprišiel celý — skús ho vložiť znova.") from exc
        if len(data) > settings.poradca_attachment_max_bytes:
            raise AttachmentRefused(
                f"Obrázok „{name}“ má {_mb(len(data))} — jeden obrázok môže mať najviac "
                f"{_mb(settings.poradca_attachment_max_bytes)}."
            )
        mime = detect(data)
        if mime is None:
            raise AttachmentRefused(
                f"„{name}“ nie je obrázok PNG, JPEG, WebP ani GIF — Poradcovi sa dajú priložiť len snímky a obrázky."
            )
        total += len(data)
        if total > settings.poradca_attachments_max_total_bytes:
            raise AttachmentRefused(
                f"Obrázky jednej otázky môžu mať spolu najviac {_mb(settings.poradca_attachments_max_total_bytes)}."
            )
        accepted.append(Incoming(name=name, data=data, mime=mime))
    return accepted


def store(conversation_id: uuid.UUID, images: list[Incoming]) -> list[dict]:
    """Write the images into the conversation's folder; return what the question message records about them.

    Each file is written under a temporary name and renamed into place, so a half-written image never lies there.
    Readable by the container's user (it runs as 1000), writable by nobody.
    """
    if not images:
        return []
    folder = sandbox.attachments_dir(conversation_id)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o755)
    stored: list[dict] = []
    try:
        for image in images:
            attachment_id = uuid.uuid4().hex
            final = folder / f"{attachment_id}.{IMAGE_TYPES[image.mime]}"
            partial = folder / f".{attachment_id}.part"
            partial.write_bytes(image.data)
            os.chmod(partial, 0o444)
            os.rename(partial, final)
            stored.append({"id": attachment_id, "name": image.name, "mime": image.mime, "size_bytes": len(image.data)})
    except OSError:
        discard(conversation_id, stored)
        raise
    return stored


def discard(conversation_id: uuid.UUID, stored: list[dict]) -> None:
    """Remove images stored for a question that was not recorded after all."""
    for meta in stored:
        path = path_of(conversation_id, meta)
        if path is not None:
            path.unlink(missing_ok=True)


def path_of(conversation_id: uuid.UUID, meta: dict) -> Optional[Path]:
    """The file of one recorded image — only ever inside the conversation's own folder."""
    attachment_id, mime = str(meta.get("id", "")), meta.get("mime")
    if len(attachment_id) != 32 or any(c not in "0123456789abcdef" for c in attachment_id) or mime not in IMAGE_TYPES:
        return None
    return sandbox.attachments_dir(conversation_id) / f"{attachment_id}.{IMAGE_TYPES[mime]}"


def container_path(meta: dict) -> str:
    return f"{sandbox.CONTAINER_ATTACHMENTS_DIR}/{meta['id']}.{IMAGE_TYPES[meta['mime']]}"


def prompt_note(stored: list[dict]) -> str:
    """What the question tells Poradca about the images — where they are and that it is to look at them."""
    if not stored:
        return ""
    lines = "\n".join(f"- {container_path(meta)} („{meta['name']}“)" for meta in stored)
    return (
        "\n\n[Manažér k otázke priložil snímky obrazovky. Skôr než odpovieš, otvor každú nástrojom Read — "
        f"sú len na čítanie:\n{lines}]"
    )

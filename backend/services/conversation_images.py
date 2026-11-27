"""DEV-534 — screenshots the Manažér pastes into the conversation with the Staviteľ, as Nápady and Poradca take them
(DEV-52).

The Director 27.11.2026, at orthodox-register's Vizuál: „…potrebujem doplniť do chatu staviteľa možnosť pridania snímku
obrazovky podobne ako sme to už spravili v prípade Nápadov.“ DEV-527 stored them as files in the project; he: „Chcel
som aby to bolo vložené presne tak ako v nápadoch, kde vidno malý obrázok priamo v chate, okrem toho ja nechcem aby
tieto snímky boli uložené do projektu ako súbory, slúžia len výhradne na to aby som vysvetli čo presne chcem a potom
už netreba.“ So:

* only images — PNG, JPEG, WebP, GIF — recognised by their CONTENT, within Poradca's limits (one check, one place:
  :func:`attachments.accept`);
* stored in the cockpit's data (``settings.conversation_images_dir``/<project>/<version>/) — never in the project,
  never in git; readable by the build container's user, writable by nobody;
* pinned to the Manažér's message they came with (``payload.images``) — the conversation shows them there;
* handed to the Staviteľ once, with the first turn after they came (:func:`prompt_note` marks them delivered); a
  sandboxed build turn has its project's folder mounted read-only (:func:`mount_args`);
* deleted when the version is done or cancelled (:func:`remove_version`) and with the project (:func:`remove_project`).
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.models.pipeline import PipelineMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services.poradca import attachments

IMAGE_TYPES = attachments.IMAGE_TYPES
NOT_AN_IMAGE = (
    "„{name}“ nie je obrázok (PNG, JPEG, WebP, GIF). Súbor, ktorý má projekt mať, prilož tlačidlom „Priložiť súbor“."
)
#: How many of the latest Manažér messages are looked at for screenshots not yet shown to the Staviteľ.
RECENT_MESSAGES = 50


def root() -> Path:
    return Path(settings.conversation_images_dir)


def project_dir(project_slug: str) -> Path:
    return root() / project_slug


def version_dir(project_slug: str, version_id: uuid.UUID) -> Path:
    return project_dir(project_slug) / str(version_id)


def slug_of(db: Session, version_id: uuid.UUID) -> str:
    return db.execute(
        select(Project.slug).join(Version, Version.project_id == Project.id).where(Version.id == version_id)
    ).scalar_one()


def accept(uploads: Iterable[dict[str, Any]]) -> list[attachments.Incoming]:
    """Decode and check the screenshots of one message — all of them, before anything is stored.

    Raises:
        attachments.AttachmentRefused: too many, too large, not base64, or not an image.
    """
    files = attachments.accept((str(one.get("name", "")), str(one.get("data", ""))) for one in uploads)
    for one in files:
        if one.mime not in IMAGE_TYPES:
            raise attachments.AttachmentRefused(NOT_AN_IMAGE.format(name=one.name))
    return files


def store(db: Session, version_id: uuid.UUID, files: list[attachments.Incoming]) -> list[dict[str, Any]]:
    """Write the screenshots into the version's folder; return what the message records about them. Each file is
    written under a temporary name and renamed into place, so a half-written file never lies there."""
    if not files:
        return []
    slug = slug_of(db, version_id)
    folder = version_dir(slug, version_id)
    folder.mkdir(parents=True, exist_ok=True)
    for directory in (folder.parent, folder):
        os.chmod(directory, 0o755)
    stored: list[dict[str, Any]] = []
    try:
        for one in files:
            image_id = uuid.uuid4().hex
            final = folder / f"{image_id}.{IMAGE_TYPES[one.mime]}"
            partial = folder / f".{image_id}.part"
            partial.write_bytes(one.data)
            os.chmod(partial, 0o444)
            os.rename(partial, final)
            stored.append({"id": image_id, "name": one.name, "mime": one.mime, "size_bytes": len(one.data)})
    except OSError:
        discard(slug, version_id, stored)
        raise
    return stored


def path_of(project_slug: str, version_id: uuid.UUID, meta: dict[str, Any]) -> Optional[Path]:
    """The file of one recorded screenshot — only ever inside the version's own folder."""
    image_id, mime = str(meta.get("id", "")), meta.get("mime")
    if len(image_id) != 32 or any(c not in "0123456789abcdef" for c in image_id) or mime not in IMAGE_TYPES:
        return None
    return version_dir(project_slug, version_id) / f"{image_id}.{IMAGE_TYPES[mime]}"


def discard(project_slug: str, version_id: uuid.UUID, stored: list[dict[str, Any]]) -> None:
    """Remove screenshots stored for a message that was not recorded after all. The project is named by the caller:
    after a refused action the transaction is rolled back, and a query then would not be the one to rely on."""
    for meta in stored:
        path = path_of(project_slug, version_id, meta)
        if path is not None:
            path.unlink(missing_ok=True)


def pin(db: Session, version_id: uuid.UUID, before: set[uuid.UUID], stored: list[dict[str, Any]]) -> bool:
    """Pin the screenshots to the Manažér's message the action has just written (not among ``before``)."""
    if not stored:
        return True
    query = select(PipelineMessage).where(PipelineMessage.version_id == version_id, PipelineMessage.author == "manazer")
    if before:
        query = query.where(PipelineMessage.id.not_in(before))
    message = db.execute(query.order_by(PipelineMessage.seq.desc()).limit(1)).scalar_one_or_none()
    if message is None:
        return False
    message.payload = {**(message.payload or {}), "images": stored}
    db.flush()
    return True


def find(db: Session, version_id: uuid.UUID, image_id: str) -> Optional[tuple[Path, dict[str, Any]]]:
    """A screenshot pinned to a message of this version — its file and what the message records."""
    rows = db.execute(
        select(PipelineMessage.payload).where(
            PipelineMessage.version_id == version_id, PipelineMessage.author == "manazer"
        )
    ).scalars()
    for payload in rows:
        for meta in (payload or {}).get("images") or []:
            if meta.get("id") == image_id:
                path = path_of(slug_of(db, version_id), version_id, meta)
                return (path, meta) if path is not None and path.is_file() else None
    return None


def prompt_note(db: Session, version_id: uuid.UUID) -> str:
    """What the Staviteľ's next turn is told about screenshots not shown to him yet — where they are, that he is to open
    each. Marks them delivered: the session keeps them, a later turn is not told again."""
    messages = (
        db.execute(
            select(PipelineMessage)
            .where(PipelineMessage.version_id == version_id, PipelineMessage.author == "manazer")
            .order_by(PipelineMessage.seq.desc())
            .limit(RECENT_MESSAGES)
        )
        .scalars()
        .all()
    )
    waiting = [
        m for m in reversed(messages) if (m.payload or {}).get("images") and not m.payload.get("images_delivered")
    ]
    if not waiting:
        return ""
    slug = slug_of(db, version_id)
    lines = []
    for message in waiting:
        for meta in message.payload["images"]:
            path = path_of(slug, version_id, meta)
            if path is not None:
                lines.append(f"- {path} („{meta.get('name', 'snímka')}“)")
        message.payload = {**message.payload, "images_delivered": True}
    db.flush()
    if not lines:
        return ""
    return (
        "[Manažér k svojej správe priložil snímky obrazovky — ukazujú, čo myslí. Skôr než začneš, otvor každú "
        "nástrojom Read; sú len na čítanie a do projektu nepatria:\n" + "\n".join(lines) + "]"
    )


def mount_args(project_slug: str) -> list[str]:
    """A sandboxed build turn's read-only mount of its project's screenshots — at the same path the note names."""
    folder = project_dir(project_slug)
    if not folder.is_dir():
        return []
    return ["--mount", f"type=bind,source={folder},target={folder},readonly"]


def remove_version(project_slug: str, version_id: uuid.UUID) -> None:
    shutil.rmtree(version_dir(project_slug, version_id), ignore_errors=True)


def remove_project(project_slug: str) -> None:
    shutil.rmtree(project_dir(project_slug), ignore_errors=True)

"""Service layer for the Credentials registry + filesystem store.

The on-disk store lives at ``settings.credentials_storage_path``
(``/opt/data/nex-studio/credentials/`` by default). Each registry row
in the ``credentials`` table is a 1:1 pointer to a flat-directory
markdown file under that path.

Per CLAUDE.md §13 the credentials API is gated by
:func:`backend.core.security.require_ri_role` at the router layer.
This service therefore does not perform an additional authorization
check; it only enforces the filesystem invariants:

* the file_path stays inside ``credentials_storage_path`` (no
  ``../`` escapes, no symlink-to-elsewhere);
* the filename is a flat name (no subdirectories) — the store is one
  flat directory by design;
* read / write size never exceeds
  ``settings.credentials_content_max_bytes``;
* writes are UTF-8; reads decode UTF-8 and reject binary.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.db.models.credentials import Credential
from backend.schemas.credentials import (
    CredentialContent,
    CredentialCreate,
)


def _storage_root() -> Path:
    """Return the resolved credentials storage root."""
    return Path(settings.credentials_storage_path).resolve()


def _ensure_storage_dir() -> Path:
    """Create the storage dir if missing (mode 0700) and return it.

    Idempotent. Mode 0700 = owner read/write/execute only — even
    other local users cannot enter the directory.
    """
    root = _storage_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _validate_filename(filename: str) -> None:
    """Reject any filename that escapes the flat directory."""
    if "/" in filename or "\\" in filename:
        raise ValueError("filename must be a flat name (no slashes)")
    if filename in ("", ".", ".."):
        raise ValueError("filename must not be empty or a directory marker")


def _validate_path_in_store(path: Path) -> None:
    """Ensure the resolved path stays inside the storage root."""
    root = _storage_root()
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path resolves outside the credentials store: {path}") from exc


def list_credentials(db: Session) -> list[Credential]:
    """Return every credentials registry row, newest first."""
    stmt = select(Credential).order_by(Credential.created_at.desc())
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, credential_id: UUID) -> Credential:
    cred = db.get(Credential, credential_id)
    if cred is None:
        raise ValueError(f"Credential {credential_id} not found")
    return cred


def create(db: Session, data: CredentialCreate) -> Credential:
    """Create a new credentials entry — DB row + on-disk file.

    Atomicity: the DB row is flushed first; the file is written second.
    A flush failure rolls back automatically; a file-write failure
    after a successful flush leaves an orphan row, which the router
    catches and translates back into a rolled-back transaction.
    """
    _validate_filename(data.filename)

    root = _ensure_storage_dir()
    target = root / data.filename
    _validate_path_in_store(target)

    if target.exists():
        raise ValueError(f"credential file already exists: {data.filename}")

    if len(data.content.encode("utf-8")) > settings.credentials_content_max_bytes:
        raise ValueError(
            f"content too large: exceeds credentials_content_max_bytes ({settings.credentials_content_max_bytes})"
        )

    cred = Credential(title=data.title, file_path=str(target))
    db.add(cred)
    db.flush()

    target.write_text(data.content, encoding="utf-8")
    target.chmod(0o600)
    return cred


def delete(db: Session, credential_id: UUID) -> None:
    """Delete the registry row AND the on-disk file."""
    cred = get_by_id(db, credential_id)
    target = Path(cred.file_path)
    _validate_path_in_store(target)

    db.delete(cred)
    db.flush()

    if target.exists():
        target.unlink()


def read_content(db: Session, credential_id: UUID) -> CredentialContent:
    """Return the on-disk content of a credentials file."""
    cred = get_by_id(db, credential_id)
    target = Path(cred.file_path)
    _validate_path_in_store(target)

    if not target.is_file():
        raise FileNotFoundError(f"credential file missing on disk: {cred.file_path}")

    size_bytes = target.stat().st_size
    if size_bytes > settings.credentials_content_max_bytes:
        raise ValueError(
            f"file too large: {size_bytes} bytes exceeds "
            f"credentials_content_max_bytes ({settings.credentials_content_max_bytes})"
        )

    raw = target.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"file is not valid UTF-8 (likely binary): {cred.file_path}") from exc

    return CredentialContent(
        credential_id=cred.id,
        file_path=cred.file_path,
        content=text,
        size_bytes=size_bytes,
    )


def write_content(db: Session, credential_id: UUID, content: str) -> CredentialContent:
    """Overwrite the on-disk content of a credentials file."""
    cred = get_by_id(db, credential_id)
    target = Path(cred.file_path)
    _validate_path_in_store(target)

    encoded = content.encode("utf-8")
    if len(encoded) > settings.credentials_content_max_bytes:
        raise ValueError(
            f"content too large: exceeds credentials_content_max_bytes ({settings.credentials_content_max_bytes})"
        )

    target.write_text(content, encoding="utf-8")
    target.chmod(0o600)

    # Touch updated_at on the row.
    db.flush()
    db.refresh(cred)

    return CredentialContent(
        credential_id=cred.id,
        file_path=cred.file_path,
        content=content,
        size_bytes=len(encoded),
    )

"""Files the Manažér hands the AI Agent — kept in the project's ``private/`` folder, never in git (DEV-44).

An agent sometimes needs a real file only a person has: a sample e-mail, an invoice, an export from another
system. Until DEV-44 the cockpit could not take one, so the agent asked for a file "in /opt/projects/<slug>/
private/ on ANDROS" and Poradca advised ``scp`` over Tailscale with an SSH key — a terminal step Tibor and Nazar
cannot do (Career Asistent 0.1.0, 09.10.2026). Now the Manažér attaches the file in the Riadiace centrum and it
lands here, in ``<project>/private/``.

Such a file is often personal (that e-mail carries codes that cancel the job agent without a login), so the one
promise this module keeps is that **git never sees it**. Before any write the folder is excluded in the
repository's local ``.git/info/exclude`` — no tracked file of the project changes — and ``git check-ignore``
then has to confirm it for the exact path; a project whose own rules pull the folder back into git is refused,
not written. The name is reduced to a plain file name inside ``private/`` (no path, no hidden file), the size is
capped by :attr:`Settings.private_file_max_bytes`, and the file gets the project's owner, so the agent's sandbox
(the same uid) can read and delete it.
"""

from __future__ import annotations

import os
import re
import subprocess
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

#: The folder in the project root the files live in — the same name the Career Asistent design already uses.
PRIVATE_DIR = "private"

#: The line ``.git/info/exclude`` carries; anchored, so only the root folder is meant.
EXCLUDE_LINE = f"/{PRIVATE_DIR}/"

#: ``git check-ignore`` is a local read — 15 s is generous.
GIT_CAP_SECONDS = 15

MAX_NAME_CHARS = 120

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_PART_SUFFIX = ".part"


class PrivateFileRefused(Exception):
    """The file was not stored (or not deleted); the message is the sentence the Manažér reads."""


@dataclass(frozen=True)
class PrivateFile:
    """A file in ``private/`` — ``path`` relative to the project root (``private/profesia.eml``)."""

    path: str
    size_bytes: int
    modified_at: datetime


def human_size(size: int) -> str:
    """``812 B`` · ``12 kB`` · ``1,4 MB`` — the size as the Manažér reads it."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} kB"
    return f"{size / (1024 * 1024):.1f}".replace(".", ",").removesuffix(",0") + " MB"


def safe_name(filename: str) -> str:
    """A plain file name for ``private/``: the last path segment, ASCII letters, digits, ``.``, ``_``, ``-``.

    ``Ponuka Profesie č. 2.eml`` → ``Ponuka-Profesie-c.-2.eml``; ``../../etc/passwd`` → ``passwd``; a leading
    dot is dropped (no hidden file), and the suffix survives shortening."""
    last = re.split(r"[\\/]", filename or "")[-1]
    ascii_name = unicodedata.normalize("NFKD", last).encode("ascii", "ignore").decode("ascii")
    name = _UNSAFE.sub("-", ascii_name).strip(".-_")
    name = re.sub(r"-{2,}", "-", name)
    if not name or name in {".", ".."}:
        name = "subor"
    if len(name) > MAX_NAME_CHARS:
        stem, dot, suffix = name.rpartition(".")
        if dot and 0 < len(suffix) <= 16:
            name = stem[: MAX_NAME_CHARS - len(suffix) - 1] + "." + suffix
        else:
            name = name[:MAX_NAME_CHARS]
    return name


def _git_ignores(project_root: Path, rel: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "check-ignore", "-q", "--", rel],
            capture_output=True,
            timeout=GIT_CAP_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrivateFileRefused(
            f"Nepodarilo sa overiť, že git súbor neuvidí ({exc.__class__.__name__}) — súbor som neuložil."
        ) from exc
    return result.returncode == 0


def ensure_ignored(project_root: Path, rel: str) -> None:
    """Make sure git never sees ``rel`` — exclude ``private/`` locally, then have git confirm it for ``rel``.

    A project without a repository has nothing to commit the file to. ``.git/info/exclude`` is the repository's
    own, untracked list, so the project's ``.gitignore`` and every tracked file stay as they are."""
    git_dir = Path(project_root) / ".git"
    if not git_dir.exists():
        return
    if git_dir.is_dir():
        exclude = git_dir / "info" / "exclude"
        existing = exclude.read_text(encoding="utf-8").splitlines() if exclude.is_file() else []
        if EXCLUDE_LINE not in (line.strip() for line in existing):
            created = not exclude.exists()
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("a", encoding="utf-8") as fh:
                if existing and existing[-1].strip():
                    fh.write("\n")
                fh.write("# NEX Studio (DEV-44): files the Manažér hands the AI Agent — never committed.\n")
                fh.write(EXCLUDE_LINE + "\n")
            if created:
                _own_like(Path(project_root), exclude.parent)
                _own_like(Path(project_root), exclude)
    if not _git_ignores(Path(project_root), rel):
        raise PrivateFileRefused(
            f"Súbor som neuložil: git by ho v projekte videl ({rel}). Pravidlá projektu vracajú priečinok "
            f"{PRIVATE_DIR}/ do gitu — to treba opraviť skôr, než sa doň dá niečo priložiť."
        )


def _own_like(project_root: Path, path: Path) -> None:
    """Give ``path`` the project's owner — the backend writes as root, the agent's sandbox reads as that uid."""
    if os.geteuid() != 0:
        return
    st = os.stat(project_root)
    os.chown(path, st.st_uid, st.st_gid)


def _private_dir(project_root: Path) -> Path:
    root = Path(project_root)
    if not root.is_dir():
        raise PrivateFileRefused("Projekt nemá na serveri pracovný priečinok — súbor sa nemá kam uložiť.")
    base = root / PRIVATE_DIR
    if base.is_symlink() or (base.exists() and not base.is_dir()):
        raise PrivateFileRefused(f"V projekte je {PRIVATE_DIR} niečo iné než priečinok — súbor som neuložil.")
    return base


def _describe(project_root: Path, path: Path) -> PrivateFile:
    st = path.stat()
    return PrivateFile(
        path=path.relative_to(project_root).as_posix(),
        size_bytes=st.st_size,
        modified_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
    )


def save(project_root: Path, filename: str, content: bytes, *, max_bytes: int) -> PrivateFile:
    """Store ``content`` as ``private/<safe name>`` (``-2``, ``-3``… when the name is taken) and describe it."""
    root = Path(project_root)
    if len(content) > max_bytes:
        raise PrivateFileRefused(
            f"Súbor má viac než {human_size(max_bytes)}, čo je najviac, čo sa dá priložiť — neuložil som ho."
        )
    if not content:
        raise PrivateFileRefused("Súbor je prázdny — nie je čo priložiť.")
    base = _private_dir(root)
    name = safe_name(filename)
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        stem, suffix = name, ""
    dest, n = base / name, 2
    while dest.exists() or dest.is_symlink():
        dest = base / (f"{stem}-{n}.{suffix}" if suffix else f"{stem}-{n}")
        n += 1
    ensure_ignored(root, dest.relative_to(root).as_posix())
    if not base.exists():
        base.mkdir(mode=0o750)
        _own_like(root, base)
    part = base / f".{dest.name}.{uuid.uuid4().hex}{_PART_SUFFIX}"
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        _own_like(root, part)
        os.replace(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return _describe(root, dest)


def list_files(project_root: Path) -> list[PrivateFile]:
    """Every regular file under ``private/`` (also what the agent put there), by path. Links are not followed."""
    root = Path(project_root)
    base = root / PRIVATE_DIR
    if not base.is_dir() or base.is_symlink():
        return []
    found: list[PrivateFile] = []
    for dirpath, _dirnames, filenames in os.walk(base, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            if name.startswith(".") and name.endswith(_PART_SUFFIX):
                continue  # an upload still being written
            if path.is_symlink() or not path.is_file():
                continue
            found.append(_describe(root, path))
    return sorted(found, key=lambda f: f.path)


def delete(project_root: Path, rel: str) -> PrivateFile:
    """Remove ``rel`` — only a regular file inside ``private/``; anything else is refused, not touched."""
    root = Path(project_root)
    base = _private_dir(root)
    target = root / rel
    inside = os.path.normpath(target).startswith(os.path.normpath(base) + os.sep)
    if not inside or target.is_symlink() or not target.is_file():
        raise PrivateFileRefused(f"Súbor {rel} v priečinku {PRIVATE_DIR}/ nie je — nie je čo zmazať.")
    described = _describe(root, target)
    target.unlink()
    return described


def answer_line(f: PrivateFile) -> str:
    """The line the screen puts into the Manažér's message — where the agent finds the file."""
    return (
        f"Priložený súbor: `{f.path}` ({human_size(f.size_bytes)}) — leží v koreni projektu "
        f"v priečinku {PRIVATE_DIR}/, mimo gitu."
    )

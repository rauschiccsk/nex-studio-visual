"""DEV-86 — NS Code: the files of a project as the cockpit shows them — read-only, without secrets.

12.10.2026 the Director follows the code and the structure of a project in VS Code on another monitor and wants it in
the cockpit, at least as nice as VS Code. What a person may see is decided here, once, for the tree, the file and the
search alike:

* **The files git knows or would add** — ``git ls-files --cached --others --exclude-standard``. What git ignores
  (``node_modules``, ``.venv``, build output, the manager's ``private/`` files of DEV-44) is not shown.
* **No secrets.** A file named like a secret (:mod:`backend.services.secret_files`, the rule Poradca hides by) is not
  listed and not served; the committed template of an ``.env`` is (it names the settings, never their values).
* **Only plain files of the project.** A symbolic link may point anywhere, a file with more than one hard link may be a
  file from outside — neither is listed. A file is served only when it is on the list, so a path outside the project,
  an ignored file or a secret is refused by the same check.
"""

from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.config.settings import settings
from backend.services import secret_files
from backend.services.project_files import PRIVATE_DIR

#: Local git reads — milliseconds; the cap only keeps a broken checkout from holding a request.
GIT_CAP_SECONDS = 15

#: How much of a file decides whether it is text — a NUL byte in it means binary, as git decides.
_SNIFF_BYTES = 8192

NOT_SERVED = "Tento súbor v projekte nie je, alebo sa v NS Code neukazuje (ignorovaný súbor či tajomstvo)."
NO_CHECKOUT = "Projekt nemá na serveri git repozitár — NS Code nemá čo ukázať."
SEARCH_TOO_SHORT = "Napíš aspoň 2 znaky."


class NotServed(LookupError):
    """The path is not one NS Code shows."""


class NoCheckout(LookupError):
    """The project has no git checkout on the server."""


def _git(project_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    if not (project_root / ".git").exists():
        raise NoCheckout(NO_CHECKOUT)
    try:
        return subprocess.run(
            ["git", "-C", str(project_root), *args], capture_output=True, timeout=GIT_CAP_SECONDS, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NoCheckout(f"{NO_CHECKOUT} ({exc.__class__.__name__})") from exc


def withheld(rel: str) -> bool:
    """Whether a path is kept out of NS Code by its name or place, before looking at the file itself."""
    name = rel.rsplit("/", 1)[-1]
    if rel.split("/", 1)[0] == PRIVATE_DIR:
        return True  # what the manager attached for the agent — even when somebody forced it into git
    return secret_files.is_secret_name(name) and not secret_files.is_template(name)


def _plain_file(project_root: Path, rel: str) -> Optional[os.stat_result]:
    """The file's ``lstat`` when it is a plain file with a single link, else ``None``."""
    try:
        st = os.lstat(project_root / rel)
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) and st.st_nlink == 1 else None


def _shown(project_root: Path) -> dict[str, os.stat_result]:
    """``{path: lstat}`` of every file NS Code shows."""
    r = _git(project_root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if r.returncode != 0:
        raise NoCheckout(NO_CHECKOUT)
    found: dict[str, os.stat_result] = {}
    for p in {p for p in r.stdout.decode("utf-8", "replace").split("\0") if p}:
        if not withheld(p) and (st := _plain_file(project_root, p)) is not None:
            found[p] = st
    return found


def files(project_root: Path) -> list[str]:
    """Every path NS Code shows, sorted — the tree, the quick open and the guard of :func:`read` all use this list."""
    return sorted(_shown(project_root))


def stamp(st: os.stat_result) -> str:
    """What changes when a file changes — its size and the time of its last write, to the nanosecond."""
    return f"{st.st_size}-{st.st_mtime_ns}"


# DEV-87 — VS Code's marks: M = changed against the last commit, U = new (git would add it).
CHANGED = "M"
NEW = "U"


def changes(project_root: Path) -> dict[str, str]:
    """``{path: M|U}`` of the working tree against the last commit, as ``git status`` sees it."""
    r = _git(project_root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if r.returncode != 0:
        raise NoCheckout(NO_CHECKOUT)
    entries = r.stdout.decode("utf-8", "replace").split("\0")
    marks: dict[str, str] = {}
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if xy[0] in "RC":
            i += 1  # ``-z``: a rename or a copy carries its source path as the next entry
        if xy == "??" or "A" in xy or xy[0] in "RC":
            marks[path] = NEW
        elif "D" not in xy:
            marks[path] = CHANGED
    return marks


@dataclass(frozen=True)
class Listing:
    head: Optional[str]
    files: list[str]
    #: ``{path: M|U}`` — only the files the list shows.
    changes: dict[str, str]
    #: ``{path: stamp}`` — the screen refetches an open file whose stamp moved.
    stamps: dict[str, str]


def listing(project_root: Path) -> Listing:
    """The list with what changed — one read for the tree, its marks and the refresh during a build."""
    shown = _shown(project_root)
    return Listing(
        head=head(project_root),
        files=sorted(shown),
        changes={p: m for p, m in changes(project_root).items() if p in shown},
        stamps={p: stamp(st) for p, st in shown.items()},
    )


def head(project_root: Path) -> Optional[str]:
    r = _git(project_root, "rev-parse", "HEAD")
    return r.stdout.decode().strip() or None if r.returncode == 0 else None


@dataclass(frozen=True)
class FileView:
    path: str
    size: int
    #: ``None`` when the file is binary or bigger than :attr:`Settings.ns_code_max_file_bytes`.
    text: Optional[str]
    binary: bool = False
    too_big: bool = False
    stamp: str = ""


def _view(rel: str, content: bytes, size: int, file_stamp: str = "") -> FileView:
    if size > settings.ns_code_max_file_bytes:
        return FileView(rel, size, None, too_big=True, stamp=file_stamp)
    if b"\0" in content[:_SNIFF_BYTES]:
        return FileView(rel, size, None, binary=True, stamp=file_stamp)
    return FileView(rel, size, content.decode("utf-8", "replace"), stamp=file_stamp)


def read(project_root: Path, rel: str) -> FileView:
    """The file at ``rel`` — only when :func:`files` lists it (:class:`NotServed` otherwise)."""
    if rel not in set(files(project_root)):
        raise NotServed(NOT_SERVED)
    path = project_root / rel
    st = _plain_file(project_root, rel)
    # Checked again at the moment of reading, and the real path must still lie in the project.
    if st is None or not path.resolve().is_relative_to(project_root.resolve()):
        raise NotServed(NOT_SERVED)
    if st.st_size > settings.ns_code_max_file_bytes:
        return FileView(rel, st.st_size, None, too_big=True, stamp=stamp(st))
    with open(path, "rb") as fh:
        content = fh.read(settings.ns_code_max_file_bytes + 1)
    return _view(rel, content, st.st_size, stamp(st))


def original(project_root: Path, rel: str, rev: str) -> Optional[FileView]:
    """DEV-87 — the file as it was at commit ``rev``, to compare with; ``None`` when it did not exist then.

    Only for a file :func:`files` shows — the same guard as :func:`read`, so a secret has no past either."""
    if rel not in set(files(project_root)):
        raise NotServed(NOT_SERVED)
    r = _git(project_root, "show", f"{rev}:{rel}")
    if r.returncode != 0:
        return None
    return _view(rel, r.stdout, len(r.stdout))


@dataclass(frozen=True)
class LastCommit:
    sha: str
    at: str
    subject: str


def last_commit(project_root: Path, rel: str) -> Optional[LastCommit]:
    """DEV-87 — the newest commit that changed the file; ``None`` for a file git does not know yet."""
    r = _git(project_root, "log", "-1", "--format=%H%x00%cI%x00%s", "--", rel)
    parts = r.stdout.decode("utf-8", "replace").strip().split("\0") if r.returncode == 0 else []
    return LastCommit(*parts) if len(parts) == 3 else None


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    text: str


@dataclass
class SearchResult:
    query: str
    hits: list[Hit] = field(default_factory=list)
    #: More lines matched than :attr:`Settings.ns_code_search_limit`; only the first ones are listed.
    truncated: bool = False


def search(project_root: Path, query: str) -> SearchResult:
    """Lines with ``query`` (plain text, any case) in the files NS Code shows — ``git grep`` over the same set."""
    query = query.strip()
    if len(query) < 2:
        raise ValueError(SEARCH_TOO_SHORT)
    shown = set(files(project_root))
    r = _git(project_root, "grep", "-n", "-I", "-i", "-F", "-z", "--untracked", "-e", query)
    if r.returncode not in (0, 1):  # 1 = nothing found
        raise NoCheckout(NO_CHECKOUT)
    result = SearchResult(query=query)
    for record in r.stdout.decode("utf-8", "replace").split("\n"):
        # ``-z``: ``path\0line\0text`` — a path may hold a colon, a NUL it cannot.
        parts = record.split("\0", 2)
        if len(parts) != 3 or parts[0] not in shown or not parts[1].isdigit():
            continue
        if len(result.hits) >= settings.ns_code_search_limit:
            result.truncated = True
            break
        result.hits.append(Hit(parts[0], int(parts[1]), parts[2][: settings.ns_code_search_line_chars]))
    return result

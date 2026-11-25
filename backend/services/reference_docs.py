"""DEV-511 — the documents from the internet a new project lists, downloaded by the cockpit into ``docs/reference/``.

25.11.2026 the brief of NEX DualBook left ten documents (XSD schemas of VAT, forms, the bank's integration manual) to
the Manažér to download by hand: the Projektant reads the internet but cannot save a file, the Staviteľ has no
internet. Director: „…mám ich vložiť ručne čo ja robiť nebudem.“ The Projektant now lists them in the brief
(:mod:`backend.services.poradca.references`) and the cockpit downloads them when the project is founded.

The address comes from the Projektant, who reads pages of strangers — so the cockpit goes only to a public address on
https, connects to the very address it checked (a second answer of the name server cannot lead it elsewhere), has the
certificate checked against the server's name, checks every redirect the same way, and keeps a file only when its
content is what its name says: a login or error page is never stored as the document. A file without a working
address is said on the screen; the Manažér can try again with one click.

What came and what did not is kept in the project, next to the documents: ``zdroje.json`` for the screen and
``ZDROJE.md`` for people and the Staviteľ (address, time, size, fingerprint).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import socket
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlsplit

import httpx

from backend.config.settings import settings
from backend.core.local_time import local_time
from backend.services.poradca.references import FOLDER, Reference

logger = logging.getLogger(__name__)

STATE_FILE = "docs/reference/zdroje.json"
SOURCES_FILE = "docs/reference/ZDROJE.md"

NOT_HTTPS = "adresa nie je https na štandardnom porte"
NO_HOST = "server {host} sa nedá nájsť"
PRIVATE = "adresa {host} vedie do neverejnej siete — kokpit z nej nesťahuje"
ANSWERED = "web odpovedal {code}"
NO_LOCATION = "web presmeroval bez adresy"
TOO_MANY_REDIRECTS = "príliš veľa presmerovaní"
TOO_BIG = "súbor je väčší ako {mb} MB"
TOO_SLOW = "sťahovanie trvalo dlhšie ako {seconds} s"
BROKEN = "spojenie zlyhalo ({why})"
EMPTY = "súbor je prázdny"
A_PAGE = "namiesto súboru {kind} prišla webová stránka (prihlásenie alebo chyba)"
NOT_WHAT = "obsah nie je {kind}"
OUTSIDE = "cesta vedie mimo priečinka docs/reference/"
NOT_WRITTEN = "súbor sa nedal zapísať do projektu ({why})"

_REDIRECTS = (301, 302, 303, 307, 308)
_ZIPS = ("docx", "xlsx", "odt", "ods", "zip")
_OLE = ("doc", "xls")
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class Refused(Exception):
    """The document was not downloaded; the message is the sentence the Manažér reads."""


@dataclass(frozen=True)
class Fetched:
    """One document after a try: where it is in the project, where it came from, and whether it came."""

    path: str
    url: str
    description: str
    ok: bool
    said: Optional[str] = None
    size: Optional[int] = None
    sha256: Optional[str] = None
    fetched_at: Optional[str] = None


# ── the internet, as the cockpit meets it (the two seams the tests replace) ───────────────────────────────────────────


def _addresses(host: str) -> list[str]:
    """Every address the name server gives for *host*. Raises ``OSError`` for a name that does not exist."""
    return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


def _client() -> httpx.Client:
    """A fresh client for one request: a connection is never shared, so every certificate is checked anew."""
    return httpx.Client(timeout=settings.reference_timeout_seconds, follow_redirects=False)


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _checked_addresses(url: str) -> tuple[str, list[str]]:
    """``(the server's name, its public addresses to connect to — IPv4 first)`` for an https *url* on its standard
    port. Every address the name has must be public: one private among them is enough to refuse."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise Refused(NOT_HTTPS) from None
    if parts.scheme != "https" or not parts.hostname or port not in (None, 443) or parts.username or parts.password:
        raise Refused(NOT_HTTPS)
    host = parts.hostname
    try:
        found = _addresses(host)
    except OSError:
        raise Refused(NO_HOST.format(host=host)) from None
    if not found:
        raise Refused(NO_HOST.format(host=host))
    if not all(_public(address) for address in found):
        raise Refused(PRIVATE.format(host=host))
    return host, sorted(found, key=lambda address: ipaddress.ip_address(address).version)


def _get(current: str, host: str, address: str, deadline: float) -> tuple[Optional[str], bytes]:
    """One request to the checked *address*: ``(where it redirects, b"")`` or ``(None, the document)``."""
    parts = urlsplit(current)
    target = parts._replace(netloc=f"[{address}]" if ":" in address else address).geturl()
    headers = {"Host": host, "User-Agent": settings.reference_user_agent, "Accept": "*/*"}
    with (
        _client() as client,
        client.stream("GET", target, headers=headers, extensions={"sni_hostname": host}) as answer,
    ):
        if answer.status_code in _REDIRECTS:
            location = answer.headers.get("location")
            if not location:
                raise Refused(NO_LOCATION)
            return urljoin(current, location), b""
        if answer.status_code != 200:
            raise Refused(ANSWERED.format(code=answer.status_code))
        data = bytearray()
        for chunk in answer.iter_bytes():
            data += chunk
            if len(data) > settings.reference_max_bytes:
                raise Refused(TOO_BIG.format(mb=settings.reference_max_bytes // (1024 * 1024)))
            if time.monotonic() > deadline:
                raise Refused(TOO_SLOW.format(seconds=settings.reference_timeout_seconds))
        return None, bytes(data)


def download(url: str) -> bytes:
    """The bytes of the document at *url* — or :class:`Refused` with the sentence why not. An address that cannot be
    connected to gives way to the next one of the same name (25.11.2026: ANDROS has no IPv6 route)."""
    deadline = time.monotonic() + settings.reference_timeout_seconds
    current = url
    for _hop in range(settings.reference_max_redirects + 1):
        host, addresses = _checked_addresses(current)
        failure: Optional[httpx.HTTPError] = None
        for address in addresses:
            try:
                location, data = _get(current, host, address, deadline)
            except httpx.ConnectError as error:
                failure = error
                continue
            except httpx.HTTPError as error:
                raise Refused(BROKEN.format(why=type(error).__name__)) from None
            break
        else:
            raise Refused(BROKEN.format(why=type(failure).__name__))
        if location is None:
            return data
        current = location
    raise Refused(TOO_MANY_REDIRECTS)


def _looks_like_a_page(data: bytes) -> bool:
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith((b"<!doctype html", b"<html")) or b"<html" in head[:512]


def content_problem(path: str, data: bytes) -> Optional[str]:
    """Why *data* is not the document *path* names — ``None`` when it is."""
    if not data:
        return EMPTY
    kind = path.rsplit(".", 1)[-1].casefold()
    if kind == "html":
        return None
    if _looks_like_a_page(data):
        return A_PAGE.format(kind=kind.upper())
    if kind == "pdf":
        ok = data.startswith(b"%PDF-")
    elif kind in _ZIPS:
        ok = data.startswith(b"PK\x03\x04")
    elif kind in _OLE:
        ok = data.startswith(_OLE_MAGIC)
    elif kind in ("xsd", "xml"):
        ok = data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<")
    else:
        ok = True
    return None if ok else NOT_WHAT.format(kind=kind.upper())


# ── into the project ──────────────────────────────────────────────────────────────────────────────────────────────


def _owned_like(path: Path, root: Path) -> None:
    """The backend runs as root; what it writes belongs to whoever owns the project (the agents work as that user)."""
    owner = root.stat()
    try:
        os.chown(path, owner.st_uid, owner.st_gid)
    except PermissionError:
        logger.warning("reference_docs: %s keeps its owner", path)


def _write(root: Path, rel: str, data: bytes) -> None:
    folder = (root / FOLDER).resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(folder):
        raise Refused(OUTSIDE)
    created = [parent for parent in reversed(target.parents) if parent.is_relative_to(root) and not parent.exists()]
    target.parent.mkdir(parents=True, exist_ok=True)
    for made in created:
        _owned_like(made, root)
    partial = target.with_name(f".{target.name}.part")
    try:
        partial.write_bytes(data)
        partial.replace(target)
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise Refused(NOT_WRITTEN.format(why=type(error).__name__)) from None
    _owned_like(target, root)


def _fetch_one(root: Path, reference: Reference) -> Fetched:
    try:
        data = download(reference.url)
        problem = content_problem(reference.path, data)
        if problem:
            raise Refused(problem)
        _write(root, reference.path, data)
    except Refused as refused:
        return Fetched(reference.path, reference.url, reference.description, False, said=str(refused))
    return Fetched(
        reference.path,
        reference.url,
        reference.description,
        True,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def _sources_text(done: list[Fetched]) -> str:
    rows = []
    for one in done:
        name = one.path.removeprefix(FOLDER)
        if one.ok:
            when = (
                local_time(datetime.fromisoformat(one.fetched_at)).strftime("%d.%m.%Y %H:%M") if one.fetched_at else ""
            )
            rows.append(f"| `{name}` | {one.description} | {one.url} | {when} | {one.size} B | `{one.sha256}` |")
        else:
            rows.append(f"| `{name}` | {one.description} | {one.url} | nestiahnuté: {one.said} | | |")
    return (
        "# Podklady z internetu\n\n"
        "Kokpit ich stiahol podľa zoznamu v zadaní projektu (`<podklady>`). Súbor sa ručne neupravuje — pri ďalšom "
        "stiahnutí ho kokpit prepíše.\n\n"
        "| Súbor | Čo to je | Zdroj | Stiahnuté | Veľkosť | SHA-256 |\n"
        "|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
    )


def _save_state(root: Path, done: list[Fetched]) -> None:
    payload = json.dumps({"references": [asdict(one) for one in done]}, ensure_ascii=False, indent=2) + "\n"
    _write(root, STATE_FILE, payload.encode("utf-8"))
    _write(root, SOURCES_FILE, _sources_text(done).encode("utf-8"))


#: The commit that keeps the documents in the project's history — never shown to the Manažér as unsaved work.
COMMIT_MESSAGE = "docs(reference): the documents from the internet the brief lists"


def _commit(root: Path) -> None:
    """Commit ``docs/reference/`` only — whatever else the project has is not touched. Best effort: when git cannot
    (no repository, an agent holds the index), the files stay and the build's next commit takes them."""
    folder = FOLDER.rstrip("/")
    for args in (["add", "--", folder], ["commit", "--quiet", "-m", COMMIT_MESSAGE, "--", folder]):
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=60, check=False)
        if done.returncode != 0:
            if "nothing to commit" not in done.stdout + done.stderr:
                logger.warning("reference_docs: git %s in %s: %s", args[0], root, (done.stderr or done.stdout)[:200])
            return


def state(root: Path) -> list[Fetched]:
    """What came and what did not, as the last download left it — ``[]`` for a project without the list."""
    path = root / STATE_FILE
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("references") or []
        return [Fetched(**row) for row in rows]
    except (OSError, ValueError, TypeError):
        return []


def fetch_into(root: Path, wanted: list[Reference]) -> list[Fetched]:
    """Download every document of *wanted* into the project at *root*; what came and what did not is kept there."""
    done = [_fetch_one(root, reference) for reference in wanted]
    _save_state(root, done)
    _commit(root)
    return done


def add_into(root: Path, wanted: list[Reference]) -> list[Fetched]:
    """DEV-513 — documents the Poradca of a founded project adds: merged with what the project has. A document that
    came from the same address stays as it is; a new one, a failed one or one with another address is downloaded."""
    done = {one.path: one for one in state(root)}
    for reference in wanted:
        before = done.get(reference.path)
        if before is not None and before.ok and before.url == reference.url:
            continue
        done[reference.path] = _fetch_one(root, reference)
    result = list(done.values())
    _save_state(root, result)
    _commit(root)
    return result


def retry_failed(root: Path) -> list[Fetched]:
    """Download again only what did not come; what came stays as it is."""
    before = state(root)
    done = [one if one.ok else _fetch_one(root, Reference(one.path, one.url, one.description)) for one in before]
    _save_state(root, done)
    _commit(root)
    return done


# ── one download per project at a time ────────────────────────────────────────────────────────────────────────────

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_of(root: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(root.resolve()), threading.Lock())


def running(root: Path) -> bool:
    """Is a download for the project at *root* going on now?"""
    return _lock_of(root).locked()


def start(root: Path, wanted: Optional[list[Reference]] = None, *, adding: bool = False) -> bool:
    """Download for the project at *root*: *wanted* (added to what it has with ``adding``), or with ``None`` again
    what did not come. ``False`` when a download for it is already going on (the second one would only repeat it) or
    the project has no folder."""
    lock = _lock_of(root)
    if not root.is_dir() or not lock.acquire(blocking=False):
        return False
    try:
        if wanted is None:
            retry_failed(root)
        elif adding:
            add_into(root, wanted)
        else:
            fetch_into(root, wanted)
    except Exception:  # noqa: BLE001 — a background run must say its failure in the log, never die silently
        logger.exception("reference_docs: the download into %s failed", root)
    finally:
        lock.release()
    return True


def start_once(root: Path, wanted: list[Reference]) -> bool:
    """The download at the founding: only for a project that has none yet — a second click of „Založiť projekt“
    returns what the first did and must not download everything again."""
    if not wanted or state(root):
        return False
    return start(root, wanted)

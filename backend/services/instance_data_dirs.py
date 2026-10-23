"""DEV-197 — the folders an installation keeps its data in exist before it starts, owned by the application's user.

A bind mount whose folder does not exist is made by Docker — as root. NEX Inbox's backend runs as uid 1000 and keeps
the invoices it receives in ``./originals``: on a machine where the folder was not there before, the application could
not write the first invoice. On ANDROS the folders were made by hand long ago. ICCINT-151 (24.09.2026) wrote the helper
for a new target, but nothing called it, so DEV-155 removed it as dead code (measured 23.10.2026). Director 23.10.2026:
„Áno, schvaľujem všetkých 14 bodov“.

The folders are made through Docker on the machine the installation runs on — the cockpit's key there speaks Docker
only (see :mod:`remote_instance`) — by a container that has this installation's folder mounted and nothing else; on
the cockpit's own machine the same way. A folder that is there already is left as it is: neither its owner nor its
content changes.
"""

from __future__ import annotations

import logging
import posixpath
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Optional

import yaml

from backend.services import remote_instance

logger = logging.getLogger(__name__)

#: How the script says which folder it made — one line each, on its standard output.
_CREATED = "created:"

#: For each folder (parents first): make it only when nothing is there, then give it to the application's user.
_SCRIPT = (
    'root="$1"; shift; '
    'for p in "$@"; do '
    '[ -e "$root/$p" ] || {{ mkdir "$root/$p" && chown {uid}:{gid} "$root/$p" && echo "{created}$p"; }} || exit 1; '
    "done"
).format(uid=remote_instance.UID, gid=remote_instance.GID, created=_CREATED)


def _bind_source(volume: Any) -> Optional[str]:
    """The host side of a bind mount, short (``./x:/y[:ro]``) or long (``type: bind``) syntax; ``None`` otherwise."""
    if isinstance(volume, str):
        source = volume.split(":", 1)[0] if ":" in volume else None
    elif isinstance(volume, dict) and volume.get("type") == "bind":
        source = volume.get("source")
    else:
        return None
    return source if isinstance(source, str) and source.startswith((".", "/")) else None


def _inside(source: str, instance_dir: PurePosixPath) -> Optional[str]:
    """*source* relative to the installation's folder — ``None`` when it is not inside it or says a variable."""
    if "$" in source:
        return None
    if source.startswith("/"):
        try:
            source = str(PurePosixPath(posixpath.normpath(source)).relative_to(instance_dir))
        except ValueError:
            return None
    rel = posixpath.normpath(source)
    return None if rel == "." or rel == ".." or rel.startswith("../") else rel


def data_dirs(compose_text: str, instance_dir: PurePosixPath) -> list[str]:
    """The installation's own folders its services mount, relative to its folder — every parent included, parents
    first. Named volumes, paths outside the installation's folder and paths with a variable are not its to make."""
    try:
        document = yaml.safe_load(compose_text) or {}
    except yaml.YAMLError:
        return []
    services = document.get("services") if isinstance(document, dict) else None
    found: set[str] = set()
    for service in (services or {}).values() if isinstance(services, dict) else []:
        for volume in (service.get("volumes") or []) if isinstance(service, dict) else []:
            source = _bind_source(volume)
            rel = _inside(source, instance_dir) if source else None
            if rel:
                parts = PurePosixPath(rel).parts
                found.update("/".join(parts[: i + 1]) for i in range(len(parts)))
    return sorted(found, key=lambda path: (path.count("/"), path))


def _prepare_cmd(instance_dir: PurePosixPath, dirs: list[str]) -> list[str]:
    """``docker run`` with only the installation's folder mounted; the folder names are arguments, never script text."""
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        f"{instance_dir}:/target",
        remote_instance.IMAGE,
        "sh",
        "-c",
        _SCRIPT,
        "sh",
        "/target",
        *dirs,
    ]


def created(stdout: str) -> list[str]:
    """The folders the script says it made."""
    return [line[len(_CREATED) :] for line in stdout.splitlines() if line.startswith(_CREATED)]


def prepare(compose: Path, *, deploy_host: Optional[str], timeout: int = 120) -> tuple[list[str], Optional[str]]:
    """Make the installation's missing data folders on the machine it runs on (``deploy_host``; ``None`` = here).

    Returns the folders made and ``None``, or why it could not be done — then the installation must not start, or
    Docker would make the folders as root. A compose that cannot be read has nothing to prepare here: the ``up`` that
    follows says what is wrong with it."""
    try:
        dirs = data_dirs(compose.read_text(encoding="utf-8"), PurePosixPath(compose.parent))
    except OSError:
        return [], None  # a defensive skip, never a new failure: ``up`` that follows names a compose it cannot read
    if not dirs:
        return [], None
    try:
        done = subprocess.run(  # noqa: S603 — fixed argv; the folder names are arguments of the script
            _prepare_cmd(PurePosixPath(compose.parent), dirs),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=remote_instance.docker_env(deploy_host),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"priečinky pre dáta sa nedali pripraviť: {exc}"
    if done.returncode != 0:
        return [], f"priečinky pre dáta sa nedali pripraviť: {(done.stdout + done.stderr).strip()[:200]}"
    made = created(done.stdout)
    if made:
        logger.info("data folders made for %s (%s): %s", compose.parent, deploy_host or "here", ", ".join(made))
    return made, None

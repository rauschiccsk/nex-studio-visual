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
import re
import subprocess
from dataclasses import dataclass
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


# ---------------------------------------------------------------------------------------------------------------------
# DEV-280 — the data belongs to the user the application runs as
# ---------------------------------------------------------------------------------------------------------------------

#: Where a database keeps its data in its container — its own image's, never the application's to own.
_DATABASE_DATA = PurePosixPath("/var/lib/postgresql")
#: How the script says whose owner it changed — one line each, on its standard output.
_CHANGED = "changed:"
#: For each ``path owner`` pair: when the path is there and has another owner, give it with its content to the owner.
_ALIGN_SCRIPT = (
    'while [ "$#" -gt 1 ]; do p="$1"; o="$2"; shift 2; '
    '[ -e "$p" ] || continue; '
    '[ "$(stat -c %u:%g "$p")" = "$o" ] || {{ chown -R "$o" "$p" && echo "{changed}$p"; }} || exit 1; '
    "done"
).format(changed=_CHANGED)
_ID_OUTPUT = re.compile(r"uid=(\d+)\S*\s+gid=(\d+)")


@dataclass(frozen=True)
class DataMount:
    """What one service of the installation keeps its data in: a folder of the installation or a Docker volume."""

    service: str
    image: str
    kind: str  # "folder" — relative to the installation's folder; "volume" — the volume's name in Docker
    source: str


def _named_volume(volume: Any, named: set[str]) -> Optional[tuple[str, str]]:
    """``(volume, target)`` when *volume* mounts one of the compose's *named* volumes."""
    if isinstance(volume, str):
        parts = volume.split(":")
        if len(parts) >= 2 and parts[0] in named:
            return parts[0], parts[1]
    elif isinstance(volume, dict) and volume.get("type", "volume") == "volume" and volume.get("source") in named:
        return str(volume["source"]), str(volume.get("target", ""))
    return None


def _target(volume: Any) -> str:
    if isinstance(volume, str):
        parts = volume.split(":")
        return parts[1] if len(parts) >= 2 else parts[0]
    return str(volume.get("target", "")) if isinstance(volume, dict) else ""


def _is_database_data(target: str) -> bool:
    path = PurePosixPath(target)
    return path == _DATABASE_DATA or _DATABASE_DATA in path.parents


def data_mounts(compose_text: str, instance_dir: PurePosixPath) -> list[DataMount]:
    """The data the installation's OWN services keep — those it builds, the application — outside a database's data
    and outside services parked behind a profile. A folder counts when it is the installation's own (:func:`data_dirs`),
    a volume as Docker names it (``<compose name>_<volume>``)."""
    try:
        document = yaml.safe_load(compose_text) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        return []
    project = str(document.get("name") or "")
    named = set(document.get("volumes") or {})
    found: list[DataMount] = []
    for name, service in document["services"].items():
        if not isinstance(service, dict) or not service.get("build") or service.get("profiles"):
            continue
        image = service.get("image")
        if not isinstance(image, str):
            continue
        for volume in service.get("volumes") or []:
            if _is_database_data(_target(volume)):
                continue
            volume_name = _named_volume(volume, named)
            if volume_name and project:
                found.append(DataMount(str(name), image, "volume", f"{project}_{volume_name[0]}"))
                continue
            source = _bind_source(volume)
            rel = _inside(source, instance_dir) if source else None
            if rel:
                found.append(DataMount(str(name), image, "folder", rel))
    return found


def _docker(cmd: list[str], deploy_host: Optional[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 — fixed argv
        cmd, capture_output=True, text=True, timeout=timeout, check=False, env=remote_instance.docker_env(deploy_host)
    )


def image_owner(image: str, *, deploy_host: Optional[str], timeout: int = 60) -> tuple[Optional[str], Optional[str]]:
    """``("uid:gid", None)`` the image runs as, ``(None, None)`` for root, ``(None, why)`` when it cannot be told.

    Asked by ``id`` in the image itself, without a network — a user given by name (``nk``) means nothing outside it."""
    # ``--pull never``: the image is the one the deploy has just built; a missing one is said, never fetched.
    cmd = ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--entrypoint", "id", image]
    done = _docker(cmd, deploy_host, timeout)
    found = _ID_OUTPUT.search(done.stdout) if done.returncode == 0 else None
    if not found:
        return (
            None,
            f"pod akým používateľom beží obraz {image}, sa nedalo zistiť: {(done.stderr or done.stdout).strip()[:200]}",
        )
    uid, gid = found.groups()
    return (None, None) if uid == "0" else (f"{uid}:{gid}", None)


def align_owners(compose: Path, *, deploy_host: Optional[str], timeout: int = 120) -> tuple[list[str], Optional[str]]:
    """Give the data folders and volumes of the installation to the user its application runs as (DEV-280).

    NEX Knowledge and Author Asistent moved from uid 10001 to 1000, the template's user: the folders and volumes their
    earlier versions made stayed 10001's, and the new version could not write into them. Only what has another owner
    changes, with its content; an application that runs as root changes nothing, a volume not made yet is made by
    Docker from the image. Returns what changed and ``None``, or why it could not be done — then the installation must
    not start."""
    instance_dir = PurePosixPath(compose.parent)
    try:
        mounts = data_mounts(compose.read_text(encoding="utf-8"), instance_dir)
    except OSError:
        return [], None
    if not mounts:
        return [], None
    try:
        owners: dict[str, Optional[str]] = {}
        for image in sorted({m.image for m in mounts}):
            owners[image], why = image_owner(image, deploy_host=deploy_host)
            if why:
                return [], f"dáta aplikácie sa nedali dať jej používateľovi: {why}"
        wanted: dict[tuple[str, str], set[str]] = {}
        for m in mounts:
            if owners[m.image]:
                wanted.setdefault((m.kind, m.source), set()).add(owners[m.image])
        listed = _docker(["docker", "volume", "ls", "--format", "{{.Name}}"], deploy_host, timeout)
        if listed.returncode != 0:
            return [], f"dáta aplikácie sa nedali dať jej používateľovi: {listed.stderr.strip()[:200]}"
        volumes = set(listed.stdout.split())
        cmd = ["docker", "run", "--rm", "--network", "none", "-v", f"{instance_dir}:/target"]
        pairs: list[str] = []
        names: dict[str, str] = {}
        for i, ((kind, source), owner) in enumerate(sorted(wanted.items())):
            if len(owner) != 1:
                continue  # two services of other users on one place — not the cockpit's to decide
            if kind == "volume":
                if source not in volumes:
                    continue
                cmd += ["-v", f"{source}:/v/{i}"]
                path = f"/v/{i}"
            else:
                path = f"/target/{source}"
            names[path] = source
            pairs += [path, next(iter(owner))]
        if not pairs:
            return [], None
        done = _docker(cmd + [remote_instance.IMAGE, "sh", "-c", _ALIGN_SCRIPT, "sh", *pairs], deploy_host, timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"dáta aplikácie sa nedali dať jej používateľovi: {exc}"
    if done.returncode != 0:
        return [], f"dáta aplikácie sa nedali dať jej používateľovi: {(done.stdout + done.stderr).strip()[:200]}"
    changed = [names[line[len(_CHANGED) :]] for line in done.stdout.splitlines() if line.startswith(_CHANGED)]
    if changed:
        logger.info("data owners aligned for %s (%s): %s", compose.parent, deploy_host or "here", ", ".join(changed))
    return changed, None

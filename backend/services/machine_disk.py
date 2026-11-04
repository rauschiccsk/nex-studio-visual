"""DEV-305 — the disk of the machine the cockpit builds on: room checked before a deploy, old images cleaned after one.

04.11.2026 the first deploy of NEX Knowledge to the PROD of ICC filled MAGER's system disk while it built a 4.4 GB image
(„no space left on device“); for a moment nothing could write there, and the Postgres of MÁGERSTAV's NEX Inbox on the
same disk restarted with a recovery („PANIC: … No space left on device“). Docker there held 22.4 GB of images — every
deployed version of every installation — and 21.6 GB of build cache: the cockpit built and never cleaned up, and never
asked whether there was room. Director 04.11.2026: „Súhlasím“.

Docker is reached as the deploy reaches it (:func:`remote_instance.docker_env` — another machine through
``DOCKER_HOST=ssh://…``, whose key allows Docker only), so the disk is read by a short-lived container that mounts
Docker's own folders read-only. The four ``_…`` primitives below are the only places that run Docker; the suite stubs
them (``tests/conftest.py``).
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from backend.config.settings import settings
from backend.services import prod_machines, remote_instance

GB = 1024**3

#: Where Docker with the containerd image store keeps the layers it unpacks (MAGER: ``driver-type
#: io.containerd.snapshotter.v1``) — beside ``DockerRootDir``, possibly on another disk — unless containerd's own
#: config names another ``root`` (MAGER since 04.11.2026: ``/data/containerd``, SERVER-148).
CONTAINERD_ROOT = "/var/lib/containerd"
CONTAINERD_CONFIG_DIR = "/etc/containerd"

#: The words Docker and the kernel fail a write with when the disk is full.
NO_SPACE = "no space left on device"

#: Docker's sizes are decimal (``docker images``: ``4.42GB``).
_UNITS = {"B": 1, "kB": 1000, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}


@dataclass(frozen=True)
class Image:
    repository: str
    tag: str
    created: str
    size: int


def _docker(args: list[str], deploy_host: Optional[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=remote_instance.docker_env(deploy_host),
    )


def free_from_df(text: str) -> Optional[int]:
    """The least free space of the filesystems in ``df -Pk`` output, in bytes; ``None`` when no line reads."""
    free = [
        int(fields[3]) * 1024
        for line in text.splitlines()[1:]
        if len(fields := line.split()) >= 6 and fields[3].isdigit()
    ]
    return min(free) if free else None


def size_from_docker(text: str) -> int:
    """``4.42GB`` → bytes; an unreadable size counts as 0."""
    m = re.fullmatch(r"\s*([0-9.]+)\s*([kKMGT]?B)\s*", text or "")
    return int(float(m.group(1)) * _UNITS[m.group(2)]) if m else 0


def images_from(text: str) -> list[Image]:
    """The lines of ``docker images`` in the format :func:`_images` asks for → images; ``<none>`` skipped."""
    found = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) == 4 and "<none>" not in fields[:2]:
            found.append(Image(fields[0], fields[1], fields[2], size_from_docker(fields[3])))
    return found


def containerd_root_from(config: str) -> Optional[str]:
    """The ``root`` of containerd's ``config.toml``; its default when the file does not set one; ``None`` when the file
    does not read."""
    try:
        root = tomllib.loads(config).get("root", CONTAINERD_ROOT)
    except tomllib.TOMLDecodeError:
        return None
    return root if isinstance(root, str) and root.startswith("/") else None


def _containerd_root(deploy_host: Optional[str]) -> Optional[str]:
    """Where containerd on the machine keeps its data — read from its config; the default when it has none."""
    read = _docker(
        [
            "run",
            "--rm",
            "--mount",
            f"type=bind,source={CONTAINERD_CONFIG_DIR},target=/c,readonly",
            remote_instance.IMAGE,
            "cat",
            "/c/config.toml",
        ],
        deploy_host,
        timeout=60,
    )
    return containerd_root_from(read.stdout) if read.returncode == 0 else CONTAINERD_ROOT


def _free_bytes(deploy_host: Optional[str]) -> Optional[int]:
    """Free bytes on the disk Docker writes images and build layers to — the least of its folders; ``None`` unknown."""
    try:
        info = _docker(["info", "--format", "{{.DockerRootDir}}|{{json .DriverStatus}}"], deploy_host, timeout=30)
        if info.returncode != 0:
            return None
        root, _, status = info.stdout.strip().partition("|")
        folders = [root]
        if "io.containerd.snapshotter" in status:
            containerd = _containerd_root(deploy_host)
            if containerd is None:
                return None
            folders.append(containerd)
        mounts = [a for i, f in enumerate(folders) for a in ("--mount", f"type=bind,source={f},target=/m{i},readonly")]
        targets = [f"/m{i}" for i in range(len(folders))]
        df = _docker(["run", "--rm", *mounts, remote_instance.IMAGE, "df", "-Pk", *targets], deploy_host)
    except (OSError, subprocess.SubprocessError):
        return None
    return free_from_df(df.stdout) if df.returncode == 0 else None


def _images(deploy_host: Optional[str]) -> Optional[list[Image]]:
    try:
        out = _docker(
            ["images", "--format", "{{.Repository}}\t{{.Tag}}\t{{.CreatedAt}}\t{{.Size}}"], deploy_host, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return images_from(out.stdout) if out.returncode == 0 else None


def _remove(reference: str, deploy_host: Optional[str]) -> bool:
    """``docker rmi`` without force: an image a container still uses stays."""
    try:
        return _docker(["rmi", reference], deploy_host, timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _prune_build_cache(cap: int, deploy_host: Optional[str]) -> bool:
    try:
        pruned = _docker(["builder", "prune", "-f", "--max-used-space", str(cap)], deploy_host, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return False
    return pruned.returncode == 0


def installation_images(compose_path: Path) -> dict[str, str]:
    """The images the cockpit built for the installation of *compose_path*, ``{repository: tag it runs}``.

    Only an image named after its service's container (``<installation>-<service>``, as the cockpit names both) — an
    image of another installation or a foreign one (``postgres``) is never among them."""
    try:
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    running = {}
    for svc in (compose.get("services") or {}).values() if isinstance(compose, dict) else []:
        image, container = (svc.get("image"), svc.get("container_name")) if isinstance(svc, dict) else (None, None)
        if isinstance(image, str) and isinstance(container, str) and ":" in image:
            repository, _, tag = image.rpartition(":")
            if repository == container:
                running[repository] = tag
    return running


def _gb(size: int) -> str:
    return f"{size / GB:.1f}".replace(".", ",") + " GB"


def room_refusal(deploy_host: Optional[str], *, installation: Path, built_here: Path) -> Optional[str]:
    """``None`` when the machine of *deploy_host* has room for the build; else the sentence the deploy stops with —
    before anything is written there or built.

    A build takes about :attr:`settings.deploy_build_space_factor` times the app's image, and
    :attr:`settings.deploy_disk_reserve_gb` stays free for the databases on the same disk. The image is the biggest of
    the installation (*installation*, its compose) on that machine, or of the same app's UAT on this machine
    (*built_here*) — a PROD is deployed only after its UAT, so a first PROD knows the size too. What cannot be measured
    stops the deploy as well: a guard that cannot look is no guard."""
    label = prod_machines.label(deploy_host)
    free = _free_bytes(deploy_host)
    if free is None:
        return (
            f"Nasadenie sa nezačalo: kokpit nevedel zmerať voľné miesto na disku Dockera na stroji {label} — bez toho "
            f"by stavba mohla zaplniť disk pod ostrými databázami. Skús to znova o chvíľu; keď to trvá, treba "
            f"skontrolovať Docker na stroji {label}."
        )
    there = installation_images(installation)
    here = installation_images(built_here)
    sizes = [i.size for i in _images(deploy_host) or [] if i.repository in there]
    if here:
        sizes += [i.size for i in (_images(None) or []) if i.repository in here]
    image = max(sizes, default=0)
    need = int(image * settings.deploy_build_space_factor) + settings.deploy_disk_reserve_gb * GB
    if free >= need:
        return None
    return (
        f"Nasadenie sa nezačalo: na stroji {label} je na disku Dockera voľných {_gb(free)}, stavba potrebuje aspoň "
        f"{_gb(need)} — obraz aplikácie má {_gb(image)} a stavba zaberie asi "
        f"{str(settings.deploy_build_space_factor).replace('.', ',')}-násobok, plus "
        f"{settings.deploy_disk_reserve_gb} GB rezerva pre databázy na tom istom disku. Požiadaj správcu stroja "
        f"{label}, aby uvoľnil miesto, a nasaď znova."
    )


def clean_after_deploy(compose_path: Path, deploy_host: Optional[str]) -> list[str]:
    """After a deploy that came up: the installation's images of older versions go — the running one and
    :attr:`settings.deploy_previous_versions_kept` before it stay (the way back, DEV-291) — and the machine's build
    cache is kept under :attr:`settings.deploy_build_cache_cap_gb`. Returns the sentences for the deploy screen; a
    cleanup that fails is said, the deploy stands."""
    running = installation_images(compose_path)
    if not running:
        return []
    label = prod_machines.label(deploy_host)
    images = _images(deploy_host)
    if images is None:
        return [f"Kokpit po nasadení nevedel prečítať obrazy na stroji {label}, staré verzie preto neodpratal."]
    before = _free_bytes(deploy_host)
    gone, kept_in_use = [], []
    for repository, tag in sorted(running.items()):
        older = sorted(
            (i for i in images if i.repository == repository and i.tag != tag), key=lambda i: i.created, reverse=True
        )
        for image in older[settings.deploy_previous_versions_kept :]:
            reference = f"{repository}:{image.tag}"
            (gone if _remove(reference, deploy_host) else kept_in_use).append(reference)
    pruned = _prune_build_cache(settings.deploy_build_cache_cap_gb * GB, deploy_host)
    after = _free_bytes(deploy_host)
    said = []
    freed = after - before if before is not None and after is not None else None
    if gone or (freed is not None and freed >= GB):
        what = (
            f"zmazal obrazy starších verzií ({len(gone)}): {', '.join(gone)}" if gone else "zmenšil medzipamäť stavieb"
        )
        said.append(
            f"Kokpit po nasadení na stroji {label} {what}"
            + (f"; voľné miesto na disku Dockera {_gb(before)} → {_gb(after)}." if freed is not None else ".")
        )
    if kept_in_use:
        said.append(f"Tieto staré obrazy na stroji {label} ostali, používa ich kontajner: {', '.join(kept_in_use)}.")
    if not pruned:
        said.append(f"Medzipamäť stavieb na stroji {label} sa nepodarilo zmenšiť.")
    return said


def said_plainly(detail: str, deploy_host: Optional[str]) -> str:
    """A deploy that failed on a full disk, said in a sentence before Docker's own words."""
    if NO_SPACE not in (detail or "").lower():
        return detail
    label = prod_machines.label(deploy_host)
    return (
        f"Na stroji {label} sa počas nasadenia minulo miesto na disku — stavba nedobehla. Požiadaj správcu stroja "
        f"{label}, aby uvoľnil miesto, a nasaď znova. Hláška Dockeru: {detail}"
    )

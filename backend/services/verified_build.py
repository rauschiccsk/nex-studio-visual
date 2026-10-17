"""A deploy builds its images from the verified commit only — never from the project's working folder (DEV-139).

**Why.** 17.10.2026 NEX Inbox 1.9.0 went to MÁGERSTAV's PROD (MAGER) and choked ANDROS's line for ~15 minutes.
``docker compose build`` sent the installation's build context — the whole working folder ``/opt/projects/nex-inbox``,
~200 MB after ``.dockerignore``, 164 MB of it the agent's own npm cache — once per service that builds: three
services, ~600 MB up a 4.6 Mbit/s uplink. Files git does not track (the agent's caches and comparisons,
``private/``) went to the customer's machine although no verification ever saw them; the tracked tree is 24 MB.

**How.** The contexts' repository exports its ``HEAD`` (``git archive`` — tracked files only, nothing uncommitted)
into a throwaway folder; an override compose points every build context there, and a service whose image another
service already builds from the same build definition does not build it again (``build: !reset null``). ``HEAD``
is the verified commit — a deploy is offered only while the code has not moved away from it (``deployability``).

**When it cannot** (contexts in two repositories, git fails, no repository) the deploy builds as before and its
report says so — the person sees it; nothing is pretended.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 120
_SOURCE_DIR = "src"
_OVERRIDE = "verified-build.override.yml"


@dataclass(frozen=True)
class VerifiedBuild:
    """The exported commit and the override that builds from it. ``root`` is removed by :func:`cleanup`."""

    root: Path
    override: Path
    commit: str
    size_bytes: int
    images: int
    reused: tuple[str, ...] = field(default_factory=tuple)

    def note(self) -> str:
        reused = f"; obraz z inej služby použije {', '.join(self.reused)}" if self.reused else ""
        return (
            f"Stavba z overeného commitu {self.commit[:10]} ({_size(self.size_bytes)}, "
            f"stavaných obrazov: {self.images}{reused})."
        )


@dataclass(frozen=True)
class Preparation:
    """``build`` None = build as the compose says: either nothing builds (``note`` empty) or the verified commit
    could not be prepared (``note`` says why — it goes into the deploy's report)."""

    build: Optional[VerifiedBuild]
    note: str = ""


@dataclass(frozen=True)
class _Builder:
    service: str
    context: Path
    image: Optional[str]
    spec: str  # the build definition without its context — two equal specs build the same image


def _size(n: int) -> str:
    return f"{n / 1024**2:.1f} MB" if n >= 1024**2 else f"{max(1, n // 1024)} kB"


def _git(cwd: Path, *args: str) -> Optional[str]:
    try:
        done = subprocess.run(
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=_GIT_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _builders(compose: Path) -> Optional[list[_Builder]]:
    """Every service that builds, with its context resolved the way compose resolves it. ``None`` = unreadable."""
    try:
        data = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return []
    builders: list[_Builder] = []
    for name, svc in services.items():
        if not isinstance(svc, dict) or "build" not in svc:
            continue
        build = svc["build"]
        if isinstance(build, str):
            build = {"context": build}
        if not isinstance(build, dict):
            return None
        context = Path(str(build.get("context", ".")))
        rest = {k: v for k, v in build.items() if k != "context"}
        builders.append(
            _Builder(
                service=str(name),
                context=(context if context.is_absolute() else compose.parent / context).resolve(),
                image=str(svc["image"]) if svc.get("image") else None,
                spec=json.dumps(rest, sort_keys=True, default=str),
            )
        )
    return builders


def _export(repo: Path, commit: str, dest: Path) -> int:
    """``git archive <commit>`` unpacked into *dest* → bytes of the tracked files. Raises on failure."""
    archive = dest.parent / "commit.tar"
    done = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", "-o", str(archive), commit],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"git archive zlyhal: {done.stderr.strip()[-200:]}")
    dest.mkdir()
    with tarfile.open(archive) as tar:
        size = sum(m.size for m in tar.getmembers() if m.isfile())
        tar.extractall(dest, filter="data")
    archive.unlink()
    return size


def _override_text(entries: list[tuple[str, Optional[Path]]]) -> str:
    """``services:`` with a new context per building service, ``!reset null`` for a service whose image is reused."""
    lines = ["services:"]
    for service, context in entries:
        lines.append(f"  {json.dumps(service)}:")
        if context is None:
            lines.append("    build: !reset null")
        else:
            lines += ["    build:", f"      context: {json.dumps(str(context))}"]
    return "\n".join(lines) + "\n"


def prepare(compose: Path) -> Preparation:
    """Export the verified commit of *compose*'s build contexts and write the override that builds from it.

    Blocking (git, tar) — call it off the event loop. Never raises."""
    builders = _builders(compose)
    if builders is None:
        return Preparation(None, "Stavba z pracovného priečinka — predpis inštalácie sa nedal prečítať.")
    if not builders:
        return Preparation(None)
    toplevels = {_git(b.context, "rev-parse", "--show-toplevel") for b in builders}
    if None in toplevels or len(toplevels) != 1:
        return Preparation(None, "Stavba z pracovného priečinka — kontexty stavby neležia v jednom repozitári gitu.")
    repo = Path(str(toplevels.pop()))
    commit = _git(repo, "rev-parse", "HEAD")
    if not commit:
        return Preparation(None, "Stavba z pracovného priečinka — commit repozitára sa nedal zistiť.")
    root = Path(tempfile.mkdtemp(prefix="nsv-verified-build-"))
    try:
        size = _export(repo, commit, root / _SOURCE_DIR)
        entries: list[tuple[str, Optional[Path]]] = []
        built: set[tuple[str, str, str]] = set()
        reused: list[str] = []
        for b in builders:
            key = (b.image or "", str(b.context), b.spec)
            if b.image and key in built:
                entries.append((b.service, None))
                reused.append(b.service)
                continue
            built.add(key)
            entries.append((b.service, root / _SOURCE_DIR / b.context.relative_to(repo)))
        override = root / _OVERRIDE
        override.write_text(_override_text(entries), encoding="utf-8")
    except (OSError, RuntimeError, ValueError, tarfile.TarError, subprocess.SubprocessError) as exc:
        shutil.rmtree(root, ignore_errors=True)
        logger.warning("verified build: commit %s z %s sa nedal pripraviť: %s", commit[:10], repo, exc)
        return Preparation(None, f"Stavba z pracovného priečinka — overený commit sa nedal pripraviť ({exc}).")
    return Preparation(
        VerifiedBuild(root, override, commit, size, images=len(builders) - len(reused), reused=tuple(reused))
    )


def cleanup(build: Optional[VerifiedBuild]) -> None:
    if build is not None:
        shutil.rmtree(build.root, ignore_errors=True)

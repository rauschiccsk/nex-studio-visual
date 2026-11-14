"""DEV-253 — nex-app-kit in a project on an app template: the release it builds with, and the opt-in upgrade.

When the Manažér founds a new version, the cockpit compares the project's nex-app-kit with the newest release and, if
the project is behind, offers the upgrade — as for nex-shared (:mod:`backend.services.nexshared`), whose steps this
module reuses. The kit lives on two sides that must move together: the frame in ``frontend/package.json`` with its
lockfile (``npm ci`` installs what the lockfile says) and the server in ``backend/pyproject.toml`` (the release's
archive). The status names both; an upgrade moves both or neither, and one commit carries it.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from backend.config.settings import settings
from backend.services import app_template, nexshared

#: The raw CHANGELOG.md of the kit — its ``## vX.Y.Z`` sections are the prompt's „Čo prinesie“.
CHANGELOG_RAW_URL = f"https://raw.githubusercontent.com/{app_template.KIT_REPO}/main/CHANGELOG.md"
#: The server's pin, read from the one spelling the founding writes (:func:`app_template.kit_server_dependency`).
_SERVER_PIN_RE = re.compile(
    re.escape(app_template.kit_server_dependency("0.0.0")).replace(r"0\.0\.0", r"(\d+\.\d+\.\d+)")
)
_PIN_VERSION_RE = re.compile(r"#v(\d+\.\d+\.\d+)$")
FRONTEND = "frontend"
SERVER_PIN_FILE = "backend/pyproject.toml"
#: DEV-383 — the server pinned as a poetry git dependency, as NEX Manager has it (converted by hand, not founded):
#: ``nex-app-kit = {git = "https://github.com/rauschiccsk/nex-app-kit.git", tag = "vX", subdirectory = "server"}``.
_POETRY_PIN_RE = re.compile(r"(?m)^nex-app-kit\s*=\s*\{(?P<body>[^}\n]*)\}")
_POETRY_TAG_RE = re.compile(r'\btag\s*=\s*"v(\d+\.\d+\.\d+)"')
_POETRY_SERVER_RE = re.compile(r'\bsubdirectory\s*=\s*"server"')
#: A poetry project builds from its lock (``poetry export``) — the upgrade re-locks it and must see it follow.
POETRY_LOCK_FILE = "backend/poetry.lock"


def _poetry_pins(text: str) -> list[tuple[re.Match, str]]:
    """Every poetry git pin of the kit's server in *text* with its release — the kit's repository, its ``server``."""
    pins = []
    for match in _POETRY_PIN_RE.finditer(text):
        body = match.group("body")
        tag = _POETRY_TAG_RE.search(body)
        if app_template.KIT_REPO in body and _POETRY_SERVER_RE.search(body) and tag:
            pins.append((match, tag.group(1)))
    return pins


def _with_poetry_tag(text: str, target_version: str) -> tuple[str, int]:
    """*text* with the kit server's poetry pin moved to *target_version* — the rest of the line as the project has
    it."""
    pins = _poetry_pins(text)
    if len(pins) != 1:
        return text, len(pins)
    match, _version = pins[0]
    line = _POETRY_TAG_RE.sub(f'tag = "v{target_version}"', match.group(0), count=1)
    return text[: match.start()] + line + text[match.end() :], 1


def lock_poetry(backend_dir: Path, *, timeout: Optional[int] = None) -> bool:
    """Re-lock a poetry project's dependencies after its kit pin moved (``poetry lock --no-update``: the other
    packages stay as locked). Network-bound (it resolves the git dependency), hence a seam the tests replace."""
    try:
        proc = subprocess.run(
            [settings.poetry_bin, "lock", "--no-update"],
            cwd=str(backend_dir),
            capture_output=True,
            text=True,
            timeout=timeout or settings.poetry_lock_timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _poetry_locked_version(lock_path: Path) -> Optional[str]:
    """The release of nex-app-kit a poetry.lock pins, or None."""
    try:
        data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    found = [p.get("version") for p in data.get("package", []) if p.get("name") == app_template.KIT_PACKAGE]
    return found[0] if len(found) == 1 else None


def server_version(source_path: str) -> Optional[str]:
    """The nex-app-kit release the server is pinned to in ``backend/pyproject.toml``, or None."""
    try:
        text = (Path(source_path) / SERVER_PIN_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    # DEV-383: the founding's spelling, or a poetry git dependency — one pin either way, never a guess between two.
    found = _SERVER_PIN_RE.findall(text) + [version for _match, version in _poetry_pins(text)]
    return found[0] if len(found) == 1 else None


def status_for_source(
    source_path: str, *, tags: Optional[list[str]] = None, changelog_text: Optional[str] = None
) -> dict:
    """The kit the project builds with vs the newest release: ``{current, latest, behind, up_to_date, changelog,
    parts}``. ``parts`` names the frame (the lockfile's version) and the server (its pin); ``current`` is the older of
    the two and None when either cannot be read — then nothing is offered, never a false number. ``tags`` and
    ``changelog_text`` are test seams; otherwise ``git ls-remote`` and the raw CHANGELOG.md are asked."""
    parts = [
        {
            "name": "frontend",
            "label": "Rám (frontend)",
            "current": nexshared.effective_version(source_path, FRONTEND, app_template.KIT_PACKAGE),
        },
        {"name": "server", "label": "Server", "current": server_version(source_path)},
    ]
    versions = [part["current"] for part in parts]
    current = None if None in versions else min(versions, key=nexshared._key)
    all_tags = tags if tags is not None else nexshared.list_remote_tags(app_template.KIT_GIT_URL)
    latest = nexshared.pick_latest(all_tags)
    behind = nexshared.count_behind(current, all_tags)
    changelog: list[dict] = []
    if behind > 0:
        text = changelog_text if changelog_text is not None else nexshared.fetch_changelog(CHANGELOG_RAW_URL)
        changelog = nexshared.parse_changelog_sections(text, current, latest)
    return {
        "current": current,
        "latest": latest,
        "behind": behind,
        "up_to_date": bool(current) and behind == 0,
        "changelog": changelog,
        "parts": parts,
    }


#: The placeholder of the kit's own pin in the scaffold's ``frontend/package.json`` (scaffold/render.py fills it).
_KIT_PLACEHOLDER = "{{KIT_FRONTEND}}"


def fetch_scaffold_package(template: str, version: str) -> Optional[dict]:
    """The packages the template's frontend needs at a release — its scaffold's ``frontend/package.json`` at the tag,
    the kit's own pin filled in. ``None`` when it cannot be read (then nothing is upgraded)."""
    raw = f"https://raw.githubusercontent.com/{app_template.KIT_REPO}/v{version}"
    url = f"{raw}/scaffold/{template}/frontend/package.json"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 — fixed https URL
            text = resp.read().decode("utf-8")
        package = json.loads(
            text.replace(_KIT_PLACEHOLDER, nexshared.git_dependency(app_template.KIT_PACKAGE, version))
        )
    except Exception:  # noqa: BLE001 — GitHub unreachable or no such release: no upgrade, never a crash
        return None
    specs = [*(package.get("dependencies") or {}).values(), *(package.get("devDependencies") or {}).values()]
    return None if any("{{" in str(spec) for spec in specs) else package


def upgrade_source(
    source_path: str,
    target_version: str,
    template: str,
    *,
    scaffold_package: Optional[Callable[[str, str], Optional[dict]]] = None,
) -> bool:
    """Move the project's nex-app-kit to ``target_version`` — the frame's pin, nex-shared raised to the release's own
    pin when older, the packages the release's frontend newly needs, the lockfile; then the server's pin (DEV-257: as
    the conversion does, DEV-254).

    The lockfile must really resolve to the target (``npm ci`` installs what it says), else every file touched is put
    back: a frame and a server on two releases would speak two versions of the standard interface. False when there
    is nothing to rewrite (no pin on either side), the release's packages cannot be read or the lockfile did not
    follow. Does NOT commit."""
    root = Path(source_path)
    pkg, lock, server = root / FRONTEND / "package.json", root / FRONTEND / "package-lock.json", root / SERVER_PIN_FILE
    poetry_lock = root / POETRY_LOCK_FILE
    try:
        originals = {
            path: path.read_text(encoding="utf-8") if path.is_file() else None
            for path in (pkg, lock, server, poetry_lock)
        }
    except OSError:
        return False
    if originals[pkg] is None or originals[server] is None:
        return False
    try:
        project_package = json.loads(originals[pkg])
    except ValueError:
        return False
    on_kit = app_template.KIT_PACKAGE in (project_package.get("dependencies") or {})
    new_server, pins = _SERVER_PIN_RE.subn(app_template.kit_server_dependency(target_version), originals[server])
    # DEV-383: a server pinned as a poetry git dependency moves in its own spelling and is re-locked below.
    poetry = pins == 0 and originals[poetry_lock] is not None
    if poetry:
        new_server, pins = _with_poetry_tag(originals[server], target_version)
    if not on_kit or pins != 1:
        return False
    release = (scaffold_package or fetch_scaffold_package)(template, target_version)
    if release is None:
        return False
    package, specs = app_template.with_template_packages(project_package, release)
    pkg.write_text(json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    server.write_text(new_server, encoding="utf-8")
    wanted = {
        name: _PIN_VERSION_RE.search(spec).group(1)
        for name in (app_template.KIT_PACKAGE, nexshared.PACKAGE)
        for spec in specs
        if spec.startswith(nexshared.git_dependency(name, ""))
    }
    frontend_followed = nexshared.resolve_lockfile(root / FRONTEND, *specs) and all(
        nexshared.effective_version(source_path, FRONTEND, name) == version for name, version in wanted.items()
    )
    server_followed = not poetry or (
        lock_poetry(root / "backend") and _poetry_locked_version(poetry_lock) == target_version
    )
    if frontend_followed and server_followed:
        return True
    for path, original in originals.items():
        if original is not None:
            path.write_text(original, encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    return False


def commit_upgrade(source_path: str, target_version: str) -> bool:
    """One commit of both sides — the frame's pin and lockfile, the server's pin."""
    return nexshared.commit_pin_upgrade(
        source_path,
        target_version,
        directories=(FRONTEND,),
        package=app_template.KIT_PACKAGE,
        # DEV-383: a poetry project's lock goes in the same commit — it is what its build installs.
        also=(SERVER_PIN_FILE, POETRY_LOCK_FILE)
        if (Path(source_path) / POETRY_LOCK_FILE).is_file()
        else (SERVER_PIN_FILE,),
    )

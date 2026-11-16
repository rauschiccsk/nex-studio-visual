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
import shutil
import subprocess
import tempfile
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
#: DEV-353 — a server whose image is built from requirements.txt (NEX Knowledge) carries the pin there too; it moves
#: with pyproject.toml or the upgrade does not happen.
REQUIREMENTS_FILE = "backend/requirements.txt"
_NAMES_THE_KIT_RE = re.compile(rf"(?mi)^\s*{re.escape(app_template.KIT_PACKAGE)}\b")


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    except OSError:
        return None


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
    packages stay as locked). Network-bound (it resolves the git dependency), hence a seam the tests replace.

    DEV-414: in a clean copy of the project's files that git tracks, never in the project itself. 16.11.2026 NEX
    Manager's upgrade to 0.15.2 recorded nex-app-kit 0.8.0: poetry reused the old checkout in the project's own
    ``backend/.venv/src/nex-app-kit`` (3.11.2026) and read the version from its ignored ``nex_app_kit.egg-info``. The
    lock written in the copy comes back to the project; nothing else does."""
    backend_dir = Path(backend_dir)
    root = backend_dir.parent
    with tempfile.TemporaryDirectory(prefix="kit-lock-") as tmp:
        work_root = Path(tmp) / root.name
        for rel in _tracked_files(root) or _poetry_files(backend_dir, root):
            source = root / rel
            if source.is_file() or source.is_symlink():
                target = work_root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target, follow_symlinks=False)
        work_backend = work_root / backend_dir.relative_to(root)
        if not (work_backend / "pyproject.toml").is_file():
            return False
        if not _run_poetry_lock(work_backend, timeout or settings.poetry_lock_timeout_seconds):
            return False
        locked = work_backend / "poetry.lock"
        if not locked.is_file():
            return False
        shutil.copy2(locked, backend_dir / "poetry.lock")
    return True


def _tracked_files(root: Path) -> Optional[list[str]]:
    """The files git tracks in ``root`` (paths relative to it), or ``None`` when it is not a git checkout."""
    try:
        proc = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [path for path in proc.stdout.decode("utf-8", "replace").split("\0") if path]


def _poetry_files(backend_dir: Path, root: Path) -> list[str]:
    """Without git: the files poetry reads to lock — nothing the project keeps beside them."""
    return [
        str((backend_dir / name).relative_to(root))
        for name in ("pyproject.toml", "poetry.lock", "poetry.toml")
        if (backend_dir / name).is_file()
    ]


def _run_poetry_lock(work_backend: Path, timeout: int) -> bool:
    """``poetry lock --no-update`` in ``work_backend`` — the step the tests replace."""
    try:
        proc = subprocess.run(
            [settings.poetry_bin, "lock", "--no-update"],
            cwd=str(work_backend),
            capture_output=True,
            text=True,
            timeout=timeout,
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
    """The nex-app-kit release the server is pinned to in ``backend/pyproject.toml``, or None — None too when
    ``backend/requirements.txt`` names the kit on another release or in a spelling the cockpit cannot read (DEV-353:
    the status does not claim a release the server's build does not install)."""
    root = Path(source_path)
    text = _read(root / SERVER_PIN_FILE)
    if text is None:
        return None
    # DEV-383: the founding's spelling, or a poetry git dependency — one pin either way, never a guess between two.
    found = _SERVER_PIN_RE.findall(text) + [version for _match, version in _poetry_pins(text)]
    if len(found) != 1:
        return None
    requirements = _read(root / REQUIREMENTS_FILE)
    if (
        requirements is not None
        and _NAMES_THE_KIT_RE.search(requirements)
        and _SERVER_PIN_RE.findall(requirements) != found
    ):
        return None
    return found[0]


def _at_head(root: Path, rel: str) -> Optional[str]:
    """The file *rel* as the project's last commit holds it, or None (no git, no such file in the commit)."""
    try:
        shown = subprocess.run(
            ["git", "-C", str(root), "show", f"HEAD:{rel}"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return shown.stdout if shown.returncode == 0 else None


def uncommitted_version(source_path: str) -> Optional[str]:
    """DEV-400 — the release of nex-app-kit the project's files name but its git does not hold yet, or None.

    Both sides (the frame's lockfile and the server's pin) must name the same release in the files; the last commit
    names another on either side. 16.11.2026 Orthodox Hermenia's upgrade to 0.15.2 stayed on disk unsaved — its commit
    was refused by the project's own pre-commit check — and nothing told the Manažér; the settings offer to save it."""
    root = Path(source_path)
    if not (root / ".git").exists():
        return None
    frame = nexshared.effective_version(source_path, FRONTEND, app_template.KIT_PACKAGE)
    if not frame or frame != server_version(source_path):
        return None
    lock = _at_head(root, f"{FRONTEND}/package-lock.json")
    pins = _at_head(root, SERVER_PIN_FILE)
    committed_frame = nexshared.parse_lock_version(lock, app_template.KIT_PACKAGE) if lock else None
    found = (_SERVER_PIN_RE.findall(pins) + [v for _m, v in _poetry_pins(pins)]) if pins else []
    committed_server = found[0] if len(found) == 1 else None
    return frame if (committed_frame, committed_server) != (frame, frame) else None


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
        # DEV-400: an upgrade the files carry but git does not — the settings offer to save it.
        "uncommitted": uncommitted_version(source_path),
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


def upgrade_problem(
    source_path: str,
    target_version: str,
    template: str,
    *,
    scaffold_package: Optional[Callable[[str, str], Optional[dict]]] = None,
) -> Optional[str]:
    """Move the project's nex-app-kit to ``target_version`` — the frame's pin, nex-shared raised to the release's own
    pin when older, the packages the release's frontend newly needs, the lockfile; then the server's pin (DEV-257: as
    the conversion does, DEV-254).

    The lockfile must really resolve to the target (``npm ci`` installs what it says), else every file touched is put
    back: a frame and a server on two releases would speak two versions of the standard interface. False when there
    is nothing to rewrite (no pin on either side), the release's packages cannot be read or the lockfile did not
    follow. Does NOT commit.

    DEV-414: ``None`` when the upgrade went through, else the step that failed and what it found, in a sentence for the
    Manažér — 16.11.2026 NEX Manager's upgrade said only „over dostupnosť GitHubu“ while its server lock had recorded
    0.8.0, and every retry would have failed the same way."""
    root = Path(source_path)
    pkg, lock, server = root / FRONTEND / "package.json", root / FRONTEND / "package-lock.json", root / SERVER_PIN_FILE
    poetry_lock, requirements = root / POETRY_LOCK_FILE, root / REQUIREMENTS_FILE
    try:
        originals = {
            path: path.read_text(encoding="utf-8") if path.is_file() else None
            for path in (pkg, lock, server, poetry_lock, requirements)
        }
    except OSError:
        return "súbory projektu sa nedajú prečítať"
    if originals[pkg] is None or originals[server] is None:
        return f"projekt nemá {FRONTEND}/package.json alebo {SERVER_PIN_FILE}"
    try:
        project_package = json.loads(originals[pkg])
    except ValueError:
        return f"{FRONTEND}/package.json nie je platný JSON"
    on_kit = app_template.KIT_PACKAGE in (project_package.get("dependencies") or {})
    new_server, pins = _SERVER_PIN_RE.subn(app_template.kit_server_dependency(target_version), originals[server])
    # DEV-383: a server pinned as a poetry git dependency moves in its own spelling and is re-locked below.
    poetry = pins == 0 and originals[poetry_lock] is not None
    if poetry:
        new_server, pins = _with_poetry_tag(originals[server], target_version)
    if not on_kit:
        return f"frontend projektu nestojí na šablóne (v {FRONTEND}/package.json chýba {app_template.KIT_PACKAGE})"
    if pins != 1:
        return f"v {SERVER_PIN_FILE} nie je šablóna servera zapísaná tak, aby ju kokpit vedel prepísať ({pins}×)"
    # DEV-353: requirements.txt that names the kit moves with pyproject.toml — or, in a spelling the cockpit cannot
    # rewrite, the upgrade stops here, before any file is touched.
    new_requirements = None
    if originals[requirements] is not None and _NAMES_THE_KIT_RE.search(originals[requirements]):
        new_requirements, required = _SERVER_PIN_RE.subn(
            app_template.kit_server_dependency(target_version), originals[requirements]
        )
        if required != 1:
            return f"{REQUIREMENTS_FILE} uvádza šablónu v zápise, ktorý kokpit nevie prepísať"
    release = (scaffold_package or fetch_scaffold_package)(template, target_version)
    if release is None:
        return f"verziu šablóny {target_version} sa nepodarilo stiahnuť z GitHubu — skús to o chvíľu znova"
    package, specs = app_template.with_template_packages(project_package, release)
    pkg.write_text(json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    server.write_text(new_server, encoding="utf-8")
    if new_requirements is not None:
        requirements.write_text(new_requirements, encoding="utf-8")
    wanted = {
        name: _PIN_VERSION_RE.search(spec).group(1)
        for name in (app_template.KIT_PACKAGE, nexshared.PACKAGE)
        for spec in specs
        if spec.startswith(nexshared.git_dependency(name, ""))
    }
    problem = _frontend_lock_problem(root, source_path, specs, wanted) or (
        _server_lock_problem(root, poetry_lock, target_version) if poetry else None
    )
    if problem is None:
        return None
    for path, original in originals.items():
        if original is not None:
            path.write_text(original, encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    return problem


def _frontend_lock_problem(root: Path, source_path: str, specs: list[str], wanted: dict[str, str]) -> Optional[str]:
    """Why the frontend's lockfile did not follow the upgrade, or ``None`` when it did."""
    if not nexshared.resolve_lockfile(root / FRONTEND, *specs):
        return "zámok frontendu (npm) sa nepodarilo prepočítať"
    for name, version in wanted.items():
        locked = nexshared.effective_version(source_path, FRONTEND, name)
        if locked != version:
            return f"zámok frontendu ukazuje {name} {locked or 'bez verzie'} namiesto {version}"
    return None


def _server_lock_problem(root: Path, poetry_lock: Path, target_version: str) -> Optional[str]:
    """Why the server's poetry.lock did not follow the upgrade, or ``None`` when it did."""
    if not lock_poetry(root / "backend"):
        return "zámok servera (poetry) sa nepodarilo prepočítať"
    locked = _poetry_locked_version(poetry_lock)
    if locked != target_version:
        return f"zámok servera zapísal {app_template.KIT_PACKAGE} {locked or 'bez verzie'} namiesto {target_version}"
    return None


def commit_upgrade(source_path: str, target_version: str) -> bool:
    """One commit of both sides — the frame's pin and lockfile, the server's pin."""
    return nexshared.commit_pin_upgrade(
        source_path,
        target_version,
        directories=(FRONTEND,),
        package=app_template.KIT_PACKAGE,
        # DEV-383: a poetry project's lock goes in the same commit — it is what its build installs; DEV-353: and so does
        # requirements.txt, which a server's image may be built from.
        also=(SERVER_PIN_FILE,)
        + tuple(rel for rel in (POETRY_LOCK_FILE, REQUIREMENTS_FILE) if (Path(source_path) / rel).is_file()),
    )

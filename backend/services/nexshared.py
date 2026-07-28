"""nex-shared version awareness — the data behind the auto-notify upgrade prompt.

Each ICC app pins nex-shared to an EXACT git tag in ``frontend/package.json``
(``github:rauschiccsk/nex-shared#vX.Y.Z``). When the Manažér founds a new app
version, the cockpit compares the app's pin against the latest published tag and,
if the app is behind, offers an opt-in upgrade (like a venv package bump). This
module supplies the comparison (:func:`status_for_source`) and the opt-in bump
(:func:`upgrade_source_pin`); the pure helpers are network-free + unit-testable,
and the one network call (``git ls-remote``) is injectable via ``tags=``.

MANIFEST vs LOCKFILE: the package.json pin is an INTENTION. Every build path installs
with ``npm ci`` (``frontend/Dockerfile``, the generated CI workflow), which obeys
``frontend/package-lock.json`` and ignores a manifest pin that disagrees with it. So
the cockpit reports the version the lockfile resolves (:func:`effective_version`) — the
one that actually builds — and an upgrade is only done when BOTH files moved.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional

#: The shared-kit remote — every app pins a tag of this repo.
NEX_SHARED_URL = "https://github.com/rauschiccsk/nex-shared.git"
#: The raw CHANGELOG.md — feeds the prompt's "Čo prinesie" (the whole point of the changelog).
CHANGELOG_RAW_URL = "https://raw.githubusercontent.com/rauschiccsk/nex-shared/main/CHANGELOG.md"

#: The lockfile entry npm writes for the installed nex-shared tree — the built version.
_LOCK_KEY = "node_modules/nex-shared"

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_PIN_RE = re.compile(r"#v?(\d+\.\d+\.\d+)")
_TAG_RE = re.compile(r"refs/tags/v(\d+\.\d+\.\d+)$")
_SECTION_RE = re.compile(r"^## v(\d+\.\d+\.\d+)\s*$", re.MULTILINE)


def parse_pin(package_json_text: str) -> Optional[str]:
    """Extract the pinned nex-shared version (``'0.11.0'``) from a package.json, or None."""
    try:
        data = json.loads(package_json_text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    dep = (data.get("dependencies") or {}).get("nex-shared") or (data.get("devDependencies") or {}).get("nex-shared")
    if not isinstance(dep, str):
        return None
    m = _PIN_RE.search(dep)
    return m.group(1) if m else None


def parse_lock_version(package_lock_text: str) -> Optional[str]:
    """Extract the RESOLVED nex-shared version (``'0.11.0'``) from a package-lock.json, or None.

    ``packages["node_modules/nex-shared"].version`` is what ``npm ci`` installs. None when the
    lockfile is unparseable / has no nex-shared entry / the version is not a bare semver — an
    unknown version must stay unknown rather than become a number the build does not use.
    """
    try:
        data = json.loads(package_lock_text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    entry = (data.get("packages") or {}).get(_LOCK_KEY)
    if not isinstance(entry, dict):
        return None
    version = entry.get("version")
    return version if isinstance(version, str) and _SEMVER.match(version) else None


def effective_version(source_path: str) -> Optional[str]:
    """The nex-shared version the app ACTUALLY builds with, from ``frontend/package-lock.json``.

    NOT the package.json pin (see the module note): the two disagree after any manual bump, and
    reporting the pin would show one version while building another. A missing / unreadable
    lockfile yields None — offer nothing, never a false number.
    """
    lock = Path(source_path) / "frontend" / "package-lock.json"
    try:
        text = lock.read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_lock_version(text)


def _key(v: str) -> tuple[int, int, int]:
    a, b, c = (int(x) for x in v.split("."))
    return (a, b, c)


def pick_latest(tags: list[str]) -> Optional[str]:
    """Highest semver among ``tags`` (bare ``X.Y.Z``), or None."""
    vs = [t for t in tags if _SEMVER.match(t)]
    return max(vs, key=_key) if vs else None


def count_behind(current: Optional[str], tags: list[str]) -> int:
    """How many published versions are newer than ``current``."""
    if not current or not _SEMVER.match(current):
        return 0
    ck = _key(current)
    return sum(1 for t in tags if _SEMVER.match(t) and _key(t) > ck)


def list_remote_tags(url: str = NEX_SHARED_URL, *, timeout: int = 30) -> list[str]:
    """The nex-shared repo's version tags via ``git ls-remote --tags`` (``[]`` on failure)."""
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--tags", url],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    out: list[str] = []
    for line in proc.stdout.splitlines():
        m = _TAG_RE.search(line.strip())
        if m:
            out.append(m.group(1))
    return out


def parse_changelog_sections(text: str, current: Optional[str], latest: Optional[str]) -> list[dict]:
    """The ``## vX.Y.Z`` changelog sections in the half-open range ``(current, latest]``.

    Returns ``[{version, body}]`` newest-first — the "Čo prinesie" the prompt renders (with its
    ``[vzhľad]``/``[API]``/``[nové]``/``[oprava]`` tags). Empty when nothing is in range.
    """
    ck = _key(current) if current and _SEMVER.match(current) else None
    lk = _key(latest) if latest and _SEMVER.match(latest) else None
    matches = list(_SECTION_RE.finditer(text))
    out: list[dict] = []
    for i, m in enumerate(matches):
        vk = _key(m.group(1))
        if (ck is not None and vk <= ck) or (lk is not None and vk > lk):
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append({"version": m.group(1), "body": text[m.end() : end].strip()})
    out.sort(key=lambda e: _key(e["version"]), reverse=True)
    return out


def fetch_changelog(url: str = CHANGELOG_RAW_URL, *, timeout: int = 15) -> str:
    """Fetch the raw CHANGELOG.md (``""`` on any failure — the prompt degrades gracefully)."""
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — fixed https URL
            return resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 — network/parse failure → no changelog, never a crash
        return ""


def status_for_source(
    source_path: str,
    *,
    tags: Optional[list[str]] = None,
    changelog_text: Optional[str] = None,
) -> dict:
    """Compare the nex-shared an app actually BUILDS with vs the latest published tag.

    ``current`` is :func:`effective_version` (the lockfile), not the package.json pin — the
    cockpit must never show a version other than the one ``npm ci`` installs.

    ``tags`` / ``changelog_text`` (test seams) skip the network; otherwise ``git ls-remote`` +
    the raw CHANGELOG.md are fetched. Returns ``{current, latest, behind, up_to_date, changelog}``
    — ``current``/``latest`` may be None (no lockfile / no reachable tags → offer nothing, never a
    false prompt); ``changelog`` = the "Čo prinesie" sections in ``(current, latest]``.
    """
    current = effective_version(source_path)
    all_tags = tags if tags is not None else list_remote_tags()
    latest = pick_latest(all_tags)
    behind = count_behind(current, all_tags)
    changelog: list[dict] = []
    if behind > 0:
        text = changelog_text if changelog_text is not None else fetch_changelog()
        changelog = parse_changelog_sections(text, current, latest)
    return {
        "current": current,
        "latest": latest,
        "behind": behind,
        "up_to_date": bool(current) and behind == 0,
        "changelog": changelog,
    }


def rewrite_pin(package_json_text: str, target_version: str) -> Optional[str]:
    """Return package.json text with the nex-shared pin set to ``#v<target>``, or None.

    None when there is no nex-shared dependency to rewrite (never invents one) or the
    target is not a bare semver. Preserves the file otherwise byte-for-byte except the pin.
    """
    if not _SEMVER.match(target_version):
        return None
    new_ref = f"{_GIT_DEP_PREFIX}{target_version}"

    def _sub(m: re.Match) -> str:
        return f'{m.group(1)}"nex-shared": "{new_ref}"'

    # Match the "nex-shared": "<anything>" entry, keep leading indentation/comma context.
    pattern = re.compile(r'(^|\{|,|\s)"nex-shared"\s*:\s*"[^"]*"')
    new_text, n = pattern.subn(_sub, package_json_text, count=1)
    return new_text if n == 1 else None


#: The single spelling of the git dependency. The manifest rewrite and the lockfile spec MUST
#: agree — a divergence would rewrite package.json to one ref and resolve the lockfile to another.
_GIT_DEP_PREFIX = "github:rauschiccsk/nex-shared#v"


def resolve_lockfile(frontend_dir: Path, spec: str, *, timeout: int = 300) -> bool:
    """Re-resolve ``package-lock.json`` to ``spec`` (``npm install --package-lock-only <spec>``).

    Lockfile only — no ``node_modules`` churn. This is the step that makes an upgrade real:
    without it ``npm ci`` keeps installing the OLD version.

    The EXPLICIT spec is load-bearing. A bare ``npm install --package-lock-only`` does not
    re-resolve a git dependency that is already present in the lockfile's virtual tree — npm
    prints "up to date", exits 0, and leaves the old commit pinned. The upgrade would then fail
    verification every single time and roll itself back, i.e. the button would never work. Naming
    the target forces the re-resolution (the same step done by hand for every manual bump).

    Network-bound (it resolves the git dep), hence a module-level seam the tests replace.
    False on any failure.
    """
    try:
        proc = subprocess.run(
            ["npm", "install", "--package-lock-only", spec],
            cwd=str(frontend_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def upgrade_source_pin(source_path: str, target_version: str) -> bool:
    """Bump the app's nex-shared to ``target_version`` — manifest AND lockfile.

    Rewrites ``frontend/package.json``, re-resolves ``frontend/package-lock.json``
    (:func:`resolve_lockfile`), then VERIFIES the lockfile really resolves to the target.
    All three must hold: builds install from the lockfile, so a manifest-only bump would
    build the old version while the cockpit reported the new one.

    Returns False when there is nothing to rewrite (no pin / bad target / missing file) or
    the lockfile did not follow — and in that case restores the files it touched, so a
    failed upgrade leaves no half-applied state behind. Does NOT commit — the caller
    decides (see :func:`commit_pin_upgrade`).
    """
    frontend = Path(source_path) / "frontend"
    pkg = frontend / "package.json"
    lock = frontend / "package-lock.json"
    if not pkg.is_file():
        return False
    original_pkg = pkg.read_text(encoding="utf-8")
    rewritten = rewrite_pin(original_pkg, target_version)
    if rewritten is None:
        return False
    # Snapshot the lockfile so a failed re-resolve can be undone. None = there was none, and in
    # that case a failed attempt REMOVES whatever npm generated: leaving a brand-new several-hundred-
    # kilobyte lockfile behind after an upgrade that reported failure is the same half-applied state
    # the rollback exists to prevent — the very next screen is the dirty-tree guard asking the Manager
    # to commit a file he never chose to create.
    try:
        original_lock = lock.read_text(encoding="utf-8") if lock.is_file() else None
    except OSError:
        return False

    pkg.write_text(rewritten, encoding="utf-8")
    spec = f"{_GIT_DEP_PREFIX}{target_version}"
    if resolve_lockfile(frontend, spec) and effective_version(source_path) == target_version:
        return True
    pkg.write_text(original_pkg, encoding="utf-8")
    if original_lock is not None:
        lock.write_text(original_lock, encoding="utf-8")
    elif lock.is_file():
        lock.unlink(missing_ok=True)
    return False


def commit_pin_upgrade(source_path: str, target_version: str, *, timeout: int = 30) -> bool:
    """Stage + commit the pin bump so it is durable + lands in the new version's build.

    Stages the lockfile alongside the manifest — ``npm ci`` reads the LOCKFILE, so a commit
    without it would ship the old version. Best-effort, never raises: no ``.git`` / nothing
    staged → returns False. A redeploy bakes the result into the nginx bundle.
    """
    root = Path(source_path)
    if not (root / ".git").is_dir():
        return False
    # Only existing paths — `git add` fails the whole call on a pathspec that matches nothing.
    paths = [p for p in ("frontend/package.json", "frontend/package-lock.json") if (root / p).is_file()]
    if not paths:
        return False
    try:
        add = subprocess.run(
            ["git", "-C", str(root), "add", *paths],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if add.returncode != 0:
            return False
        commit = subprocess.run(
            ["git", "-C", str(root), "commit", "-m", f"chore(deps): bump nex-shared → v{target_version}"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return commit.returncode == 0

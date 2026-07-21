"""nex-shared version awareness — the data behind the auto-notify upgrade prompt.

Each ICC app pins nex-shared to an EXACT git tag in ``frontend/package.json``
(``github:rauschiccsk/nex-shared#vX.Y.Z``). When the Manažér founds a new app
version, the cockpit compares the app's pin against the latest published tag and,
if the app is behind, offers an opt-in upgrade (like a venv package bump). This
module supplies the comparison (:func:`status_for_source`) and the opt-in bump
(:func:`upgrade_source_pin`); the pure helpers are network-free + unit-testable,
and the one network call (``git ls-remote``) is injectable via ``tags=``.
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
    """Compare an app's pinned nex-shared vs the latest published tag.

    ``tags`` / ``changelog_text`` (test seams) skip the network; otherwise ``git ls-remote`` +
    the raw CHANGELOG.md are fetched. Returns ``{current, latest, behind, up_to_date, changelog}``
    — ``current``/``latest`` may be None (no pin / no reachable tags → offer nothing, never a
    false prompt); ``changelog`` = the "Čo prinesie" sections in ``(current, latest]``.
    """
    pkg = Path(source_path) / "frontend" / "package.json"
    current = parse_pin(pkg.read_text(encoding="utf-8")) if pkg.is_file() else None
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
    new_ref = f"github:rauschiccsk/nex-shared#v{target_version}"

    def _sub(m: re.Match) -> str:
        return f'{m.group(1)}"nex-shared": "{new_ref}"'

    # Match the "nex-shared": "<anything>" entry, keep leading indentation/comma context.
    pattern = re.compile(r'(^|\{|,|\s)"nex-shared"\s*:\s*"[^"]*"')
    new_text, n = pattern.subn(_sub, package_json_text, count=1)
    return new_text if n == 1 else None


def upgrade_source_pin(source_path: str, target_version: str) -> bool:
    """Rewrite the app's ``frontend/package.json`` nex-shared pin to ``target_version``.

    Returns True on a successful write, False when there is nothing to rewrite (no pin /
    bad target / missing file). Does NOT commit — the caller decides (a new version's
    build stages it).
    """
    pkg = Path(source_path) / "frontend" / "package.json"
    if not pkg.is_file():
        return False
    rewritten = rewrite_pin(pkg.read_text(encoding="utf-8"), target_version)
    if rewritten is None:
        return False
    pkg.write_text(rewritten, encoding="utf-8")
    return True


def commit_pin_upgrade(source_path: str, target_version: str, *, timeout: int = 30) -> bool:
    """Stage + commit the pin bump so it is durable + lands in the new version's build.

    Best-effort, never raises: no ``.git`` / nothing staged → returns False. The build's
    ``npm ci`` reads the committed pin; a redeploy bakes it into the nginx bundle.
    """
    root = Path(source_path)
    if not (root / ".git").is_dir():
        return False
    try:
        add = subprocess.run(
            ["git", "-C", str(root), "add", "frontend/package.json"],
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

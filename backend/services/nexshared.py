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

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_PIN_RE = re.compile(r"#v?(\d+\.\d+\.\d+)")
_TAG_RE = re.compile(r"refs/tags/v(\d+\.\d+\.\d+)$")


def parse_pin(package_json_text: str) -> Optional[str]:
    """Extract the pinned nex-shared version (``'0.11.0'``) from a package.json, or None."""
    try:
        data = json.loads(package_json_text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    dep = (data.get("dependencies") or {}).get("nex-shared") or (
        data.get("devDependencies") or {}
    ).get("nex-shared")
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


def status_for_source(source_path: str, *, tags: Optional[list[str]] = None) -> dict:
    """Compare an app's pinned nex-shared vs the latest published tag.

    ``tags`` (test seam) skips the network; otherwise ``git ls-remote`` is called.
    Returns ``{current, latest, behind, up_to_date}`` — ``current``/``latest`` may be
    None (no pin / no reachable tags → offer nothing, never a false prompt).
    """
    pkg = Path(source_path) / "frontend" / "package.json"
    current = parse_pin(pkg.read_text(encoding="utf-8")) if pkg.is_file() else None
    all_tags = tags if tags is not None else list_remote_tags()
    latest = pick_latest(all_tags)
    behind = count_behind(current, all_tags)
    return {
        "current": current,
        "latest": latest,
        "behind": behind,
        "up_to_date": bool(current) and behind == 0,
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

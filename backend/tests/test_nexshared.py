"""nex-shared version awareness (v4.0.24) — the data behind the auto-notify prompt."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from backend.services import nexshared

_PKG = {
    "name": "demo-frontend",
    "dependencies": {
        "react": "^19.0.0",
        "nex-shared": "github:rauschiccsk/nex-shared#v0.11.0",
    },
}
_TAGS = ["0.9.0", "0.11.0", "0.14.0", "0.15.0"]


def _lock(version: str) -> str:
    """A package-lock.json shaped like npm's — only the nex-shared entry matters here."""
    return json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "demo-frontend"},
                "node_modules/react": {"version": "19.0.0"},
                "node_modules/nex-shared": {
                    "version": version,
                    "resolved": f"git+https://github.com/rauschiccsk/nex-shared.git#{version}",
                },
            },
        },
        indent=2,
    )


def _seed(root: Path, *, pin: str = "0.11.0", locked: str | None = "0.11.0") -> Path:
    """Write a frontend/ with a manifest pin and (optionally) a lockfile."""
    fe = root / "frontend"
    fe.mkdir(parents=True, exist_ok=True)
    pkg = dict(_PKG)
    pkg["dependencies"] = {**_PKG["dependencies"], "nex-shared": f"github:rauschiccsk/nex-shared#v{pin}"}
    (fe / "package.json").write_text(json.dumps(pkg, indent=2), encoding="utf-8")
    if locked is not None:
        (fe / "package-lock.json").write_text(_lock(locked), encoding="utf-8")
    return fe


def test_parse_pin_reads_the_version() -> None:
    assert nexshared.parse_pin(json.dumps(_PKG)) == "0.11.0"


def test_parse_pin_none_when_absent_or_bad() -> None:
    assert nexshared.parse_pin(json.dumps({"dependencies": {"react": "^19"}})) is None
    assert nexshared.parse_pin("not json at all") is None


def test_pick_latest_and_count_behind() -> None:
    assert nexshared.pick_latest(_TAGS) == "0.15.0"
    assert nexshared.count_behind("0.11.0", _TAGS) == 2  # 0.14.0 + 0.15.0
    assert nexshared.count_behind("0.15.0", _TAGS) == 0
    assert nexshared.count_behind(None, _TAGS) == 0


_CHANGELOG = (
    "# Changelog\n\n"
    "## v0.15.0\n- `[vzhľad]` slovenské labely\n\n"
    "## v0.14.0\n- `[nové]` voliteľný email\n\n"
    "## v0.11.0\n- `[oprava]` staré\n"
)


def test_parse_lock_version_reads_the_resolved_version() -> None:
    assert nexshared.parse_lock_version(_lock("0.14.0")) == "0.14.0"
    assert nexshared.parse_lock_version(json.dumps({"packages": {"": {}}})) is None
    assert nexshared.parse_lock_version(json.dumps({"packages": {"node_modules/nex-shared": {}}})) is None
    assert nexshared.parse_lock_version("not json at all") is None


def test_status_reports_what_builds_not_what_is_pinned(tmp_path: Path) -> None:
    # The manifest claims 0.15.0 but `npm ci` installs the LOCKED 0.11.0 — the cockpit must
    # show 0.11.0, or it displays one version while building another.
    _seed(tmp_path, pin="0.15.0", locked="0.11.0")
    st = nexshared.status_for_source(str(tmp_path), tags=_TAGS)
    assert st["current"] == "0.11.0"
    assert st["behind"] == 2 and st["up_to_date"] is False


def test_status_offers_nothing_without_a_lockfile(tmp_path: Path) -> None:
    # A pin alone does not tell us what builds → unknown stays unknown, never the manifest number.
    _seed(tmp_path, pin="0.11.0", locked=None)
    st = nexshared.status_for_source(str(tmp_path), tags=_TAGS)
    assert st["current"] is None and st["behind"] == 0 and st["up_to_date"] is False


def test_status_for_source_reports_the_gap(tmp_path: Path) -> None:
    _seed(tmp_path)
    st = nexshared.status_for_source(str(tmp_path), tags=_TAGS, changelog_text=_CHANGELOG)
    assert st["current"] == "0.11.0" and st["latest"] == "0.15.0"
    assert st["behind"] == 2 and st["up_to_date"] is False
    # "Čo prinesie" = the (0.11.0, 0.15.0] sections, newest first — NOT the 0.11.0 section.
    assert [c["version"] for c in st["changelog"]] == ["0.15.0", "0.14.0"]


def test_parse_changelog_sections_range_is_half_open() -> None:
    secs = nexshared.parse_changelog_sections(_CHANGELOG, "0.11.0", "0.15.0")
    assert [s["version"] for s in secs] == ["0.15.0", "0.14.0"]  # excludes current 0.11.0
    assert "vzhľad" in secs[0]["body"]
    # No current pin → everything up to latest.
    allsecs = nexshared.parse_changelog_sections(_CHANGELOG, None, "0.15.0")
    assert [s["version"] for s in allsecs] == ["0.15.0", "0.14.0", "0.11.0"]


def test_status_up_to_date_when_on_latest(tmp_path: Path) -> None:
    _seed(tmp_path, pin="0.15.0", locked="0.15.0")
    st = nexshared.status_for_source(str(tmp_path), tags=_TAGS)
    assert st["behind"] == 0 and st["up_to_date"] is True


def test_status_never_false_prompts_without_pin_or_tags(tmp_path: Path) -> None:
    # No frontend/ at all → nothing to offer (current None, behind 0).
    st = nexshared.status_for_source(str(tmp_path), tags=_TAGS)
    assert st["current"] is None and st["behind"] == 0 and st["up_to_date"] is False
    # A resolved version but no reachable tags → offer nothing.
    _seed(tmp_path)
    st2 = nexshared.status_for_source(str(tmp_path), tags=[])
    assert st2["current"] == "0.11.0" and st2["latest"] is None and st2["behind"] == 0


def test_rewrite_pin_updates_only_the_pin() -> None:
    text = json.dumps(_PKG, indent=2)
    out = nexshared.rewrite_pin(text, "0.15.0")
    assert out is not None
    assert "github:rauschiccsk/nex-shared#v0.15.0" in out
    assert "#v0.11.0" not in out
    # React (and everything else) untouched.
    assert '"react": "^19.0.0"' in out


def test_rewrite_pin_none_when_no_dep_or_bad_target() -> None:
    assert nexshared.rewrite_pin(json.dumps({"dependencies": {"react": "^19"}}), "0.15.0") is None
    assert nexshared.rewrite_pin(json.dumps(_PKG), "not-a-version") is None


def _resolver(writes: str | None):
    """Stand-in for the npm call: `writes` = the version the lockfile ends up at (None = failure)."""

    def _resolve(frontend_dir: Path, spec: str, **_kwargs) -> bool:
        # The explicit spec is the whole point: a bare `npm install --package-lock-only` leaves an
        # already-present git dep untouched, so the upgrade would verify-fail and roll back forever.
        assert spec.startswith("github:rauschiccsk/nex-shared#v"), spec
        if writes is None:
            return False
        (frontend_dir / "package-lock.json").write_text(_lock(writes), encoding="utf-8")
        return True

    return _resolve


def test_upgrade_source_pin_moves_manifest_and_lockfile(tmp_path: Path, monkeypatch) -> None:
    fe = _seed(tmp_path)
    monkeypatch.setattr(nexshared, "resolve_lockfile", _resolver("0.15.0"))
    assert nexshared.upgrade_source_pin(str(tmp_path), "0.15.0") is True
    assert nexshared.parse_pin((fe / "package.json").read_text(encoding="utf-8")) == "0.15.0"
    # The one that decides what gets built.
    assert nexshared.effective_version(str(tmp_path)) == "0.15.0"
    # No file → False (nothing to do), never a crash.
    assert nexshared.upgrade_source_pin(str(tmp_path / "nope"), "0.15.0") is False


def test_upgrade_fails_and_rolls_back_when_lockfile_cannot_be_resolved(tmp_path: Path, monkeypatch) -> None:
    fe = _seed(tmp_path)
    monkeypatch.setattr(nexshared, "resolve_lockfile", _resolver(None))
    assert nexshared.upgrade_source_pin(str(tmp_path), "0.15.0") is False
    # Manifest restored — a failed upgrade leaves no half-applied state to be committed later.
    assert nexshared.parse_pin((fe / "package.json").read_text(encoding="utf-8")) == "0.11.0"
    assert nexshared.effective_version(str(tmp_path)) == "0.11.0"


def test_upgrade_fails_when_lockfile_did_not_reach_the_target(tmp_path: Path, monkeypatch) -> None:
    # npm exited 0 but resolved something else — reporting success here would mean the cockpit
    # claims 0.15.0 while `npm ci` installs 0.14.0.
    fe = _seed(tmp_path)
    monkeypatch.setattr(nexshared, "resolve_lockfile", _resolver("0.14.0"))
    assert nexshared.upgrade_source_pin(str(tmp_path), "0.15.0") is False
    assert nexshared.parse_pin((fe / "package.json").read_text(encoding="utf-8")) == "0.11.0"
    assert nexshared.effective_version(str(tmp_path)) == "0.11.0"


def test_commit_pin_upgrade_stages_the_lockfile(tmp_path: Path, monkeypatch) -> None:
    def _git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True, text=True, check=True)

    _seed(tmp_path)
    _git("init", "-q")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test")
    _git("add", "-A")
    _git("commit", "-q", "-m", "initial")

    monkeypatch.setattr(nexshared, "resolve_lockfile", _resolver("0.15.0"))
    assert nexshared.upgrade_source_pin(str(tmp_path), "0.15.0") is True
    assert nexshared.commit_pin_upgrade(str(tmp_path), "0.15.0") is True

    committed = subprocess.run(
        ["git", "-C", str(tmp_path), "show", "--name-only", "--pretty=format:", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    # The lockfile is what `npm ci` reads — committing the manifest alone would ship the old version.
    assert "frontend/package-lock.json" in committed
    assert "frontend/package.json" in committed

"""nex-shared version awareness (v4.0.24) — the data behind the auto-notify prompt."""

from __future__ import annotations

import json
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


def test_status_for_source_reports_the_gap(tmp_path: Path) -> None:
    fe = tmp_path / "frontend"
    fe.mkdir()
    (fe / "package.json").write_text(json.dumps(_PKG), encoding="utf-8")
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
    fe = tmp_path / "frontend"
    fe.mkdir()
    pkg = {"dependencies": {"nex-shared": "github:rauschiccsk/nex-shared#v0.15.0"}}
    (fe / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    st = nexshared.status_for_source(str(tmp_path), tags=_TAGS)
    assert st["behind"] == 0 and st["up_to_date"] is True


def test_status_never_false_prompts_without_pin_or_tags(tmp_path: Path) -> None:
    # No frontend/package.json → nothing to offer (current None, behind 0).
    st = nexshared.status_for_source(str(tmp_path), tags=_TAGS)
    assert st["current"] is None and st["behind"] == 0 and st["up_to_date"] is False
    # A pin but no reachable tags → offer nothing.
    fe = tmp_path / "frontend"
    fe.mkdir()
    (fe / "package.json").write_text(json.dumps(_PKG), encoding="utf-8")
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


def test_upgrade_source_pin_writes_the_file(tmp_path: Path) -> None:
    fe = tmp_path / "frontend"
    fe.mkdir()
    (fe / "package.json").write_text(json.dumps(_PKG, indent=2), encoding="utf-8")
    assert nexshared.upgrade_source_pin(str(tmp_path), "0.15.0") is True
    assert nexshared.parse_pin((fe / "package.json").read_text(encoding="utf-8")) == "0.15.0"
    # No file → False (nothing to do), never a crash.
    assert nexshared.upgrade_source_pin(str(tmp_path / "nope"), "0.15.0") is False

"""Tests for :mod:`templates.error_codes_drift_check`.

Pure-function tests against the report builder — no subprocess invokes.
Fixtures use :data:`pytest.tmp_path` to materialise small mock files
that exercise the parser, the legacy-stub check, the FE snapshot count
check, and the BE unknown-ref check.
"""

from __future__ import annotations

from pathlib import Path

from templates.error_codes_drift_check import (
    build_report,
    parse_codes,
    render_report,
)

CANONICAL_BASE = """\
# Mock ERROR_CODES

### NIB-001 — `EMAIL_NO_PDF`
- Závažnosť: error

### NIB-002 — `EMAIL_PARSE_FAILED`
- Závažnosť: error

### NIB-010 — `PDF_ENCRYPTED`
- Závažnosť: error
"""


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_codes_counts_unique(tmp_path: Path) -> None:
    canonical = _write(tmp_path / "canonical.md", CANONICAL_BASE)
    codes, duplicates = parse_codes(canonical, "NIB")
    assert codes == ["NIB-001", "NIB-002", "NIB-010"]
    assert duplicates == []


def test_parse_codes_filters_other_prefix(tmp_path: Path) -> None:
    mixed = CANONICAL_BASE + "\n### NEX-100 — `OTHER_PROJECT`\n- Závažnosť: error\n"
    canonical = _write(tmp_path / "mixed.md", mixed)
    codes_nib, _ = parse_codes(canonical, "NIB")
    codes_nex, _ = parse_codes(canonical, "NEX")
    assert codes_nib == ["NIB-001", "NIB-002", "NIB-010"]
    assert codes_nex == ["NEX-100"]


def test_parse_codes_detects_duplicates(tmp_path: Path) -> None:
    dup = CANONICAL_BASE + "\n### NIB-001 — `DUPLICATE_HEADING`\n- foo\n"
    canonical = _write(tmp_path / "dup.md", dup)
    codes, duplicates = parse_codes(canonical, "NIB")
    assert codes == ["NIB-001", "NIB-002", "NIB-010"]
    assert duplicates == ["NIB-001"]


def test_legacy_stub_violation(tmp_path: Path) -> None:
    canonical = _write(tmp_path / "canonical.md", CANONICAL_BASE)
    legacy = _write(
        tmp_path / "legacy.md",
        "# Legacy\n\n### NIB-007 — `SHOULD_BE_GONE`\n- Závažnosť: error\n",
    )
    report = build_report(canonical, "NIB", legacy_path=legacy, fe_snapshot_path=None, be_errors_path=None)
    assert report.has_drift is True
    assert report.legacy_violations == ["NIB-007"]
    rendered = render_report(report, canonical, "NIB")
    assert "legacy not-stub" in rendered


def test_fe_snapshot_count_match(tmp_path: Path) -> None:
    canonical = _write(
        tmp_path / "canonical.md",
        "### NIB-001 — `A`\n\n### NIB-002 — `B`\n",
    )
    fe = _write(
        tmp_path / "fe.ts",
        '"NIB-001"\n"NIB-002"\n// duplicate ref\n"NIB-001"\n',
    )
    report = build_report(canonical, "NIB", legacy_path=None, fe_snapshot_path=fe, be_errors_path=None)
    assert report.fe_snapshot_count == 2
    assert report.fe_snapshot_mismatch is False
    assert report.has_drift is False


def test_fe_snapshot_count_mismatch(tmp_path: Path) -> None:
    canonical = _write(
        tmp_path / "canonical.md",
        "### NIB-001 — `A`\n\n### NIB-002 — `B`\n",
    )
    fe = _write(
        tmp_path / "fe.ts",
        '"NIB-001"\n"NIB-002"\n"NIB-999"\n',
    )
    report = build_report(canonical, "NIB", legacy_path=None, fe_snapshot_path=fe, be_errors_path=None)
    assert report.fe_snapshot_count == 3
    assert report.fe_snapshot_mismatch is True
    assert report.has_drift is True


def test_be_unknown_ref(tmp_path: Path) -> None:
    canonical = _write(tmp_path / "canonical.md", "### NIB-001 — `A`\n")
    be = _write(
        tmp_path / "errors.py",
        'NIB_001 = _NibSpec(code="NIB-001")\nNIB_099 = _NibSpec(code="NIB-099")\n',
    )
    report = build_report(canonical, "NIB", legacy_path=None, fe_snapshot_path=None, be_errors_path=be)
    assert report.be_unknown_refs == ["NIB-099"]
    assert report.has_drift is True


def test_happy_path(tmp_path: Path) -> None:
    canonical = _write(
        tmp_path / "canonical.md",
        "### NIB-001 — `A`\n\n### NIB-002 — `B`\n",
    )
    legacy = _write(tmp_path / "legacy.md", "# Legacy stub — see canonical.\n")
    fe = _write(tmp_path / "fe.ts", '"NIB-001"\n"NIB-002"\n')
    be = _write(tmp_path / "errors.py", 'code="NIB-001"\ncode="NIB-002"\n')
    report = build_report(canonical, "NIB", legacy_path=legacy, fe_snapshot_path=fe, be_errors_path=be)
    assert report.has_drift is False
    rendered = render_report(report, canonical, "NIB")
    assert "PASS — no drift detected" in rendered


def test_word_boundary_prevents_prefix_collision(tmp_path: Path) -> None:
    """``NIB-10`` (heading) must not also match the digits of ``NIB-100``."""
    canonical = _write(
        tmp_path / "canonical.md",
        "### NIB-10 — `SHORT`\n\n### NIB-100 — `LONG`\n",
    )
    codes, duplicates = parse_codes(canonical, "NIB")
    assert codes == ["NIB-10", "NIB-100"]
    assert duplicates == []

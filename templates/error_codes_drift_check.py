"""ERROR_CODES.md drift detection — projekt-agnostic ICC tool.

Detekuje drift medzi canonical ERROR_CODES.md, legacy stub, FE snapshot,
a BE errors module. Motivácia: CR-045 incident v nex-inbox (Designer čítal
legacy ``docs/specs/ERROR_CODES.md`` namiesto canonical
``docs/specs/versions/v0.1.0/spec/ERROR_CODES.md`` → NIB-114 collision).

ENV configuration
-----------------

==================  ========  ====================================================
Variable            Required  Purpose
==================  ========  ====================================================
``CANONICAL_PATH``  yes       Abs/rel path k canonical ERROR_CODES.md
``CODE_PREFIX``     yes       Prefix bez pomlčky, napr. ``NIB``, ``NEX``, ``NSI``
``LEGACY_PATH``     no        Validuje že je stub (žiadne ``### PREFIX-`` lines)
``FE_SNAPSHOT_PATH``  no      Cross-check unique-code count == canonical
``BE_ERRORS_PATH``  no        Cross-check každý ``PREFIX-NNN`` ref existuje v canonical
==================  ========  ====================================================

Exit codes
----------

* ``0`` no drift
* ``1`` drift detected (human-readable report on stdout)
* ``2`` config error (missing required env)

Usage
-----

::

    CANONICAL_PATH=docs/specs/versions/v0.1.0/spec/ERROR_CODES.md \\
    CODE_PREFIX=NIB \\
    python -m templates.error_codes_drift_check
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DriftReport:
    """Outcome of the drift check — empty fields == no problem."""

    canonical_count: int = 0
    canonical_codes: set[str] = field(default_factory=set)
    duplicates_in_canonical: list[str] = field(default_factory=list)
    legacy_violations: list[str] = field(default_factory=list)
    fe_snapshot_count: int | None = None
    fe_snapshot_mismatch: bool = False
    be_unknown_refs: list[str] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(
            self.duplicates_in_canonical or self.legacy_violations or self.fe_snapshot_mismatch or self.be_unknown_refs
        )


def parse_codes(path: Path, prefix: str) -> tuple[list[str], list[str]]:
    """Return ``(ordered_codes, duplicate_codes)`` for headings ``### PREFIX-NNN``.

    The ``\\b`` word boundary prevents ``NIB-10`` from accidentally matching
    when the actual heading is ``NIB-100``.
    """
    pattern = re.compile(rf"^### ({re.escape(prefix)})-(\d+)\b", re.MULTILINE)
    codes: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for m in pattern.finditer(path.read_text(encoding="utf-8")):
        code = f"{m.group(1)}-{m.group(2)}"
        if code in seen:
            duplicates.append(code)
        else:
            seen.add(code)
            codes.append(code)
    return codes, duplicates


def check_legacy_stub(path: Path, prefix: str) -> list[str]:
    """Return list of ``PREFIX-NNN`` headings found in legacy.

    A correctly stubbed legacy file has zero such headings — anything
    non-empty is a drift signal.
    """
    pattern = re.compile(rf"^### ({re.escape(prefix)})-(\d+)\b", re.MULTILINE)
    return [f"{m.group(1)}-{m.group(2)}" for m in pattern.finditer(path.read_text(encoding="utf-8"))]


def count_fe_snapshot(path: Path, prefix: str) -> int:
    """Return unique ``PREFIX-NNN`` code count in FE snapshot file.

    Counts unique codes regardless of how many times each appears (the
    snapshot typically references each code more than once — e.g.
    ``code: "NIB-001"`` plus a map key).
    """
    pattern = re.compile(rf"({re.escape(prefix)})-(\d+)")
    return len({f"{m.group(1)}-{m.group(2)}" for m in pattern.finditer(path.read_text(encoding="utf-8"))})


def find_be_refs(path: Path, prefix: str) -> set[str]:
    """Return set of ``PREFIX-NNN`` references in BE errors module source."""
    pattern = re.compile(rf"({re.escape(prefix)})-(\d+)")
    return {f"{m.group(1)}-{m.group(2)}" for m in pattern.finditer(path.read_text(encoding="utf-8"))}


def build_report(
    canonical_path: Path,
    prefix: str,
    legacy_path: Path | None,
    fe_snapshot_path: Path | None,
    be_errors_path: Path | None,
) -> DriftReport:
    """Run all configured checks and assemble a :class:`DriftReport`."""
    report = DriftReport()
    codes, duplicates = parse_codes(canonical_path, prefix)
    report.canonical_count = len(codes)
    report.canonical_codes = set(codes)
    report.duplicates_in_canonical = duplicates

    if legacy_path is not None:
        report.legacy_violations = check_legacy_stub(legacy_path, prefix)

    if fe_snapshot_path is not None:
        report.fe_snapshot_count = count_fe_snapshot(fe_snapshot_path, prefix)
        report.fe_snapshot_mismatch = report.fe_snapshot_count != report.canonical_count

    if be_errors_path is not None:
        refs = find_be_refs(be_errors_path, prefix)
        report.be_unknown_refs = sorted(refs - report.canonical_codes)

    return report


def render_report(report: DriftReport, canonical_path: Path, prefix: str) -> str:
    """Render a human-readable summary suitable for CI logs."""
    lines: list[str] = []
    lines.append(f"ERROR_CODES drift report — prefix {prefix}")
    lines.append(f"Canonical: {canonical_path} → {report.canonical_count} unique codes")
    if report.duplicates_in_canonical:
        lines.append(f"  DRIFT duplicate {prefix} codes: {sorted(set(report.duplicates_in_canonical))}")
    if report.legacy_violations:
        lines.append(
            f"  DRIFT legacy not-stub: found {len(report.legacy_violations)} "
            f"`### {prefix}-` headings (expected 0 for stub)"
        )
    if report.fe_snapshot_count is not None:
        marker = "DRIFT" if report.fe_snapshot_mismatch else "ok"
        lines.append(f"  {marker} FE snapshot codes: {report.fe_snapshot_count} (canonical {report.canonical_count})")
    if report.be_unknown_refs:
        lines.append(f"  DRIFT BE unknown refs: {report.be_unknown_refs}")
    if not report.has_drift:
        lines.append("  PASS — no drift detected")
    return "\n".join(lines)


def _env_path(name: str, *, required: bool) -> Path | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        if required:
            print(f"ERROR: env {name} is required", file=sys.stderr)
            sys.exit(2)
        return None
    return Path(raw)


def main() -> int:
    canonical = _env_path("CANONICAL_PATH", required=True)
    prefix = os.environ.get("CODE_PREFIX")
    if not prefix:
        print("ERROR: env CODE_PREFIX is required", file=sys.stderr)
        return 2
    legacy = _env_path("LEGACY_PATH", required=False)
    fe = _env_path("FE_SNAPSHOT_PATH", required=False)
    be = _env_path("BE_ERRORS_PATH", required=False)
    assert canonical is not None  # required=True path exits before returning None
    report = build_report(canonical, prefix, legacy, fe, be)
    print(render_report(report, canonical, prefix))
    return 1 if report.has_drift else 0


if __name__ == "__main__":
    sys.exit(main())

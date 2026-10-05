"""No model VERSION is written into the cockpit — only the family (ICCINT-167).

Director 05.10.2026: *„aby som nemusel opravovať aplikáciu, ak sa zmení verzia modelu."* The cockpit stores
and dispatches the family (``opus`` / ``sonnet`` / ``haiku``); ``claude --model <family>`` runs that family's
newest version, and the CLI updates itself on the host. A full id such as ``claude-opus-5`` written anywhere
the application reads from would pin it again — exactly what kept every build on Opus 5 after Opus 5.5
shipped. The label "which version last ran" comes from the run record, not from the code.

The guard walks the whole repository and asks "is a pinned version left anywhere?", not "is this list of
files clean?" — a new file is covered the moment it exists. Excluded are only places that RECORD what
happened (``docs/``) and the test suites, whose fixtures replay real CLI output (``modelUsage`` carries the
full id by nature).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PINNED = re.compile(r"claude-(?:opus|sonnet|haiku|fable)-\d")

# Whole subtrees that are history or test fixtures, or not ours (dependencies, build output, caches).
_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    ".pytest_cache",
    ".ruff_cache",
    "coverage",
}
_SKIP_PREFIXES = (
    "docs/",
    "tests/",
    "backend/tests/",
    "frontend/src/__tests__/",
    # Generated locally from the backend (gitignored) — clean whenever the backend is.
    "frontend/openapi.json",
)
_TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".json", ".yml", ".yaml", ".toml", ".md", ".sh", ".txt", ".cfg"}


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT).as_posix()
        if any(part in _SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if rel.startswith(_SKIP_PREFIXES) or not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
            continue
        files.append(path)
    return files


def test_the_scan_reaches_the_places_a_model_is_chosen():
    # A guard that silently scans nothing is worse than none: prove it reaches the files that pick a model.
    scanned = {p.relative_to(ROOT).as_posix() for p in _scanned_files()}
    for must in (
        "backend/schemas/user_agent_setting.py",
        "backend/services/orchestrator.py",
        "frontend/src/pages/SettingsPage.tsx",
        "frontend/src/pages/MetricsPage.tsx",
        "templates/ai-agent-settings.json",
    ):
        assert must in scanned, must


def test_no_model_version_is_pinned_anywhere():
    hits = []
    for path in _scanned_files():
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if PINNED.search(line):
                hits.append(f"{path.relative_to(ROOT).as_posix()}:{n}: {line.strip()[:120]}")
    assert not hits, "Verzia modelu je zapísaná natvrdo — patrí sem rodina (opus/sonnet/haiku):\n" + "\n".join(hits)

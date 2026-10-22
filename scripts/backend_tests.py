"""The cockpit's backend tests — the whole suite as the gate, or only the tests a change touches (DEV-184).

    poetry run python scripts/backend_tests.py --all   # the gate (CI): every test, in parallel, with the coverage floor
    poetry run python scripts/backend_tests.py         # a change: the tests that run the changed code (one run)

22.10.2026 every change, one line included, ran the whole suite twice — 15½ minutes on the developer's machine and as
long again in CI — and the suite grows with every feature. Director: „Neexistuje nejaký rozumný manažment testov, ktorý
by dokázal spustiť testy, ktoré majú zmysel?“ Measured on ANDROS: the whole suite in 8 parallel runs takes 2½ minutes
(16 run out of the test database's locks); the selection (pytest-testmon remembers which code every test ran) took
81 to 1 713 tests for that day's changes.

The selection follows Python code only. A change to anything else — templates, the agents' charters, data files,
database migrations, the tests' fixtures (``conftest.py``), dependencies — runs the whole suite: no record says which
tests read it. The gate in CI always runs everything; nothing is deployed without it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Parallel runs of the whole suite — 8 measured best on ANDROS (16 exhaust the test database's locks).
WORKERS = 8
#: The coverage floor of the gate; it may only ever be raised, never lowered to make a run pass.
COVERAGE_FLOOR = 88

#: Changed paths the selection cannot follow: it knows only which Python code a test ran.
_NOT_FOLLOWED_DIRS = ("migrations/",)
_NOT_FOLLOWED_NAMES = ("conftest.py",)


def whole_suite(extra: list[str]) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        "-n",
        str(WORKERS),
        "--tb=short",
        "-q",
        "--cov=backend",
        "--cov-report=term",
        f"--cov-fail-under={COVERAGE_FLOOR}",
        *extra,
    ]


#: The selection keeps its record in pytest's cache — a run that switches the cache off stops with an internal error.
_CACHE_OFF = ("-p", "no:cacheprovider")


def without_cache_off(extra: list[str]) -> list[str]:
    """``extra`` without ``-p no:cacheprovider`` — pytest-testmon needs the cache it switches off (22.10.2026:
    „INTERNALERROR … KeyError: 'lf'“)."""
    out: list[str] = []
    skip = False
    for i, arg in enumerate(extra):
        if skip:
            skip = False
            continue
        if arg == _CACHE_OFF[0] and i + 1 < len(extra) and extra[i + 1] == _CACHE_OFF[1]:
            skip = True
            continue
        if arg == f"{_CACHE_OFF[0]}{_CACHE_OFF[1]}":
            continue
        out.append(arg)
    return out


def selection(extra: list[str]) -> list[str]:
    """One run, as pytest-testmon supports it: under ``-n`` with no record yet it ran NO test and passed (22.10.2026) —
    the first run in a checkout runs everything once and keeps the record, the next ones only what a change touches."""
    return [sys.executable, "-m", "pytest", "--testmon", "--tb=short", "-q", "--no-cov", *without_cache_off(extra)]


def changed_paths(root: Path = ROOT) -> list[str]:
    """What differs from the last commit — changed, staged and new files (not ignored ones)."""

    def git(*args: str) -> list[str]:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout
        return [line for line in out.splitlines() if line]

    return sorted(set(git("diff", "--name-only", "HEAD")) | set(git("ls-files", "--others", "--exclude-standard")))


def not_followed(paths: list[str]) -> list[str]:
    """The changed paths no selection can answer for — any of them runs the whole suite."""
    return [
        p
        for p in paths
        if not p.endswith(".py") or p.startswith(_NOT_FOLLOWED_DIRS) or Path(p).name in _NOT_FOLLOWED_NAMES
    ]


def command(run_all: bool, paths: list[str], extra: list[str]) -> tuple[list[str], str]:
    """The pytest command for this run and the sentence that says why."""
    if run_all:
        return whole_suite(extra), "the whole suite (the gate)"
    unfollowed = not_followed(paths)
    if unfollowed:
        listed = ", ".join(unfollowed[:5]) + (f" and {len(unfollowed) - 5} more" if len(unfollowed) > 5 else "")
        return whole_suite(extra), f"the whole suite — the selection cannot follow: {listed}"
    return selection(extra), "the tests the changed code runs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--all", action="store_true", help="every test, as the gate in CI runs them")
    args, extra = parser.parse_known_args(argv)
    cmd, why = command(args.all, [] if args.all else changed_paths(), extra)
    print(f"backend tests: {why}", flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())

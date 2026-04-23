"""Tests for :mod:`backend.services.live_documents` generators and
persistence, and :mod:`backend.schemas.live_documents` DTOs.

Covers:

* ``_ordinal`` / ``_format_duration`` / ``_filter_arch_files`` helpers
  across their numeric and pattern edge cases.
* ``generate_history_entry`` for done / failed / audit-fail / multi-
  attempt task completions.
* ``generate_architect_entry`` including the empty-string rule for
  failed tasks without commits and the filter that drops markdown,
  tests and caches from ``changed_files``.
* ``generate_phase_summary_entry`` for pass / fail / NA audit and CI
  outcomes.
* ``append_history`` / ``append_architect`` / ``append_phase_summary``
  persistence end-to-end against a real :class:`KnowledgeBaseWriter`
  rooted at ``tmp_path`` — no test touches the real KB.
* ``writer=None`` mode — pure generation, no I/O.

No database fixture — the generators are DB-agnostic and the persistence
tests use a filesystem-scoped writer. STATUS.md generation (the only
DB-bound piece) ships in a follow-up step with its own DB-backed tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.schemas.live_documents import FeatCompletionData, TaskCompletionData
from backend.services.knowledge_base_writer import KnowledgeBaseWriter
from backend.services.live_documents import (
    LiveDocumentService,
    _filter_arch_files,
    _format_duration,
    _ordinal,
)


def _task(**overrides: Any) -> TaskCompletionData:
    """Build a ``TaskCompletionData`` with sensible defaults."""
    defaults: dict[str, Any] = {
        "feat_number": 1,
        "task_number": 2,
        "task_title": "Repository setup",
        "status": "done",
        "duration_seconds": 103.7,
        "agent": "ubuntu-cc",
        "commit_hashes": ["b8fa302deadbeef"],
        "changed_files": [
            "backend/app.py",
            "backend/config.py",
            "tests/test_app.py",
        ],
        "timestamp": datetime(2026, 4, 23, 14, 32, 0, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return TaskCompletionData(**defaults)


def _feat(**overrides: Any) -> FeatCompletionData:
    """Build a ``FeatCompletionData`` with sensible defaults."""
    defaults: dict[str, Any] = {
        "feat_number": 1,
        "feat_title": "Foundation",
        "total_tasks": 5,
        "duration_seconds": 600.0,
        "audit_result": "pass",
        "ci_result": "pass",
        "timestamp": datetime(2026, 4, 23, 15, 0, 0, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return FeatCompletionData(**defaults)


# ── _ordinal ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (1, "1st"),
        (2, "2nd"),
        (3, "3rd"),
        (4, "4th"),
        (10, "10th"),
        (11, "11th"),
        (12, "12th"),
        (13, "13th"),
        (21, "21st"),
        (22, "22nd"),
        (23, "23rd"),
        (100, "100th"),
        (101, "101st"),
        (111, "111th"),
        (121, "121st"),
    ],
)
def test_ordinal(n: int, expected: str) -> None:
    assert _ordinal(n) == expected


# ── _format_duration ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0s"),
        (1, "1s"),
        (30, "30s"),
        (59, "59s"),
        (60, "1m0s"),
        (75, "1m15s"),
        (600, "10m0s"),
        (3599, "59m59s"),
        (3600, "1h0m"),
        (3665, "1h1m"),
        (7200, "2h0m"),
        (7262, "2h1m"),
    ],
)
def test_format_duration(seconds: float, expected: str) -> None:
    assert _format_duration(seconds) == expected


# ── _filter_arch_files ────────────────────────────────────────────────


def test_filter_keeps_arch_extensions() -> None:
    files = ["backend/app.py", "frontend/App.tsx", "migrations/001.sql"]
    assert _filter_arch_files(files) == files


def test_filter_preserves_order() -> None:
    files = ["z.py", "a.py", "m.sql"]
    assert _filter_arch_files(files) == files


def test_filter_excludes_markdown() -> None:
    assert _filter_arch_files(["README.md", "docs/arch.md", "backend/app.py"]) == [
        "backend/app.py",
    ]


def test_filter_excludes_tests_by_prefix() -> None:
    assert _filter_arch_files(["tests/test_app.py", "backend/app.py"]) == [
        "backend/app.py",
    ]


def test_filter_excludes_tests_by_infix() -> None:
    assert _filter_arch_files(["src/foo.test.ts", "src/bar_test.ts", "src/app.ts"]) == [
        "src/app.ts",
    ]


def test_filter_excludes_pycache() -> None:
    assert _filter_arch_files(
        ["__pycache__/app.cpython-312.pyc", "backend/app.py"]
    ) == ["backend/app.py"]


def test_filter_excludes_node_modules() -> None:
    assert _filter_arch_files(["node_modules/foo/index.js", "frontend/App.tsx"]) == [
        "frontend/App.tsx",
    ]


def test_filter_keeps_docker_and_makefile() -> None:
    assert _filter_arch_files(
        ["Dockerfile", "docker-compose.yml", "Makefile", "random.txt"]
    ) == ["Dockerfile", "docker-compose.yml", "Makefile"]


def test_filter_rejects_unknown_extensions() -> None:
    assert _filter_arch_files(["README.txt", "image.png", "data.json"]) == []


def test_filter_empty_list() -> None:
    assert _filter_arch_files([]) == []


# ── generate_history_entry ────────────────────────────────────────────


def test_history_entry_happy_path() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_history_entry(_task())

    assert "14:32" in entry
    assert "Task 1.2" in entry
    assert "✅" in entry
    assert "Repository setup" in entry
    assert "103.7s" in entry
    assert "b8fa302" in entry  # first 7 chars of commit
    assert "deadbeef" not in entry  # tail should be trimmed
    assert "Code Review: PASS" in entry
    assert "Audit: PASS" in entry
    assert "1st attempt" in entry
    assert entry.endswith("\n")


def test_history_entry_failure_drops_commit_suffix() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_history_entry(_task(status="failed", commit_hashes=[]))

    assert "❌" in entry
    assert "Task 1.2" in entry
    assert "b8fa302" not in entry  # no commit prefix when no commits


def test_history_entry_audit_fail() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_history_entry(_task(audit_passed=False))

    assert "Audit: FAIL" in entry
    assert "Code Review: PASS" in entry


def test_history_entry_code_review_fail() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_history_entry(_task(code_review_passed=False))

    assert "Code Review: FAIL" in entry


def test_history_entry_multiple_attempts() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_history_entry(_task(auto_fix_attempts=2))

    assert "3rd attempt" in entry


def test_history_entry_first_attempt_default() -> None:
    svc = LiveDocumentService("nex-test")
    # auto_fix_attempts defaults to 0 → 1st attempt
    entry = svc.generate_history_entry(_task())

    assert "1st attempt" in entry


# ── generate_architect_entry ──────────────────────────────────────────


def test_architect_entry_with_files_and_commit() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_architect_entry(_task())

    assert "### Task 1.2: Repository setup" in entry
    assert "Files: backend/app.py, backend/config.py" in entry
    assert "tests/test_app.py" not in entry  # test file filtered out
    assert "Commits: b8fa302deadbeef" in entry  # full hash in architect log


def test_architect_entry_without_changed_files() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_architect_entry(_task(changed_files=[]))

    assert "### Task 1.2: Repository setup" in entry
    assert "Files:" not in entry
    assert "Commits: b8fa302deadbeef" in entry


def test_architect_entry_failed_no_commits_returns_empty() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_architect_entry(_task(status="failed", commit_hashes=[]))

    assert entry == ""


def test_architect_entry_failed_with_commits_is_recorded() -> None:
    """A failed task that committed partial work still leaves a trail."""
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_architect_entry(
        _task(status="failed", commit_hashes=["abc1234"])
    )

    assert "### Task 1.2: Repository setup" in entry
    assert "Commits: abc1234" in entry


def test_architect_entry_all_changed_files_filtered_out() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_architect_entry(
        _task(changed_files=["README.md", "tests/test_x.py"])
    )

    assert "### Task 1.2: Repository setup" in entry
    assert "Files:" not in entry
    assert "Commits:" in entry


def test_architect_entry_multiple_commits_joined() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_architect_entry(_task(commit_hashes=["aaa", "bbb", "ccc"]))

    assert "Commits: aaa, bbb, ccc" in entry


# ── generate_phase_summary_entry ──────────────────────────────────────


def test_phase_summary_green() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_phase_summary_entry(_feat())

    assert "15:00" in entry
    assert "Feat 1 COMPLETE" in entry
    assert "Foundation" in entry
    assert "Tasks: 5" in entry
    assert "Duration: 10m0s" in entry
    assert "Audit: PASS" in entry
    assert "CI: GREEN" in entry
    assert "=" * 50 in entry
    assert entry.endswith("\n")


def test_phase_summary_red_ci() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_phase_summary_entry(_feat(ci_result="fail"))

    assert "CI: RED" in entry


def test_phase_summary_na_results() -> None:
    """NEX Studio has no remote CI yet (CLAUDE.md §2.4) — NA must render."""
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_phase_summary_entry(_feat(audit_result="na", ci_result="na"))

    assert "Audit: NA" in entry
    assert "CI: N/A" in entry


def test_phase_summary_audit_fail() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_phase_summary_entry(_feat(audit_result="fail"))

    assert "Audit: FAIL" in entry


def test_phase_summary_long_duration() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_phase_summary_entry(_feat(duration_seconds=7265))

    assert "Duration: 2h1m" in entry


# ── persistence (writer plumbing) ─────────────────────────────────────


def test_append_history_writes_file_with_header(tmp_path: Path) -> None:
    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("nex-test", writer=writer)

    svc.append_history(_task())

    content = writer.read("nex-test", "HISTORY.md")
    assert content.startswith("# nex-test — History")
    assert "Task 1.2" in content
    assert "Code Review: PASS" in content


def test_append_architect_writes_file_with_header(tmp_path: Path) -> None:
    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("nex-test", writer=writer)

    svc.append_architect(_task())

    content = writer.read("nex-test", "ARCHITECT.md")
    assert content.startswith("# nex-test — Architecture Log")
    assert "### Task 1.2: Repository setup" in content


def test_append_architect_skips_when_entry_empty(tmp_path: Path) -> None:
    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("nex-test", writer=writer)

    svc.append_architect(_task(status="failed", commit_hashes=[]))

    assert writer.exists("nex-test", "ARCHITECT.md") is False


def test_append_phase_summary_goes_to_history(tmp_path: Path) -> None:
    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("nex-test", writer=writer)

    svc.append_phase_summary(_feat())

    content = writer.read("nex-test", "HISTORY.md")
    assert "Feat 1 COMPLETE" in content
    assert "=" * 50 in content


def test_append_history_dedup_on_replay(tmp_path: Path) -> None:
    """Replaying the same task completion is idempotent via writer dedup."""
    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("nex-test", writer=writer)
    data = _task()

    svc.append_history(data)
    svc.append_history(data)

    content = writer.read("nex-test", "HISTORY.md")
    # First line of history entry starts "14:32 Task 1.2 …"; dedup on first line.
    assert content.count("14:32 Task 1.2") == 1


def test_append_history_multiple_tasks_ordered(tmp_path: Path) -> None:
    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("nex-test", writer=writer)

    svc.append_history(_task(task_number=1, task_title="First"))
    svc.append_history(_task(task_number=2, task_title="Second"))

    content = writer.read("nex-test", "HISTORY.md")
    assert content.index("First") < content.index("Second")


def test_writer_none_mode_does_no_io() -> None:
    """Without a writer, append_* methods are silent no-ops."""
    svc = LiveDocumentService("nex-test")  # writer=None

    # None of these should raise.
    svc.append_history(_task())
    svc.append_architect(_task())
    svc.append_phase_summary(_feat())


def test_generators_do_not_require_writer() -> None:
    """Generators are pure functions of their data — writer is optional."""
    svc = LiveDocumentService("nex-test")

    assert svc.generate_history_entry(_task())
    assert svc.generate_architect_entry(_task())
    assert svc.generate_phase_summary_entry(_feat())


# ── schema immutability ───────────────────────────────────────────────


def test_task_completion_data_is_frozen() -> None:
    data = _task()
    with pytest.raises(Exception):  # noqa: PT011 — pydantic v2 frozen raises ValidationError
        data.task_title = "mutated"  # type: ignore[misc]


def test_feat_completion_data_is_frozen() -> None:
    data = _feat()
    with pytest.raises(Exception):  # noqa: PT011
        data.feat_title = "mutated"  # type: ignore[misc]

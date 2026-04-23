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

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select as sa_select

from backend.db.models.delegations import Delegation, ExecutionLog
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.schemas.live_documents import (
    FeatCompletionData,
    ModuleEventData,
    TaskCompletionData,
)
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


# ── generate_module_event_entry ───────────────────────────────────────


def _module_event(**overrides: Any) -> ModuleEventData:
    defaults: dict[str, Any] = {
        "event_type": "created",
        "module_code": "MM",
        "module_name": "Manažér modulov",
        "category": "Systém",
        "timestamp": datetime(2026, 4, 23, 19, 20, 0, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return ModuleEventData(**defaults)


def test_module_event_created_format() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_module_event_entry(_module_event())

    assert entry == "19:20 Module MM created — Manažér modulov (Systém)\n"


def test_module_event_deleted_format() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_module_event_entry(_module_event(event_type="deleted"))

    assert entry == "19:20 Module MM deleted — Manažér modulov\n"


def test_module_event_status_changed_format() -> None:
    svc = LiveDocumentService("nex-test")
    entry = svc.generate_module_event_entry(
        _module_event(
            event_type="status_changed",
            old_status="planned",
            new_status="in_development",
        )
    )

    assert entry == "19:20 Module MM status planned → in_development\n"


def test_append_module_event_persists_to_history(tmp_path: Path) -> None:
    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("nex-test", writer=writer)

    svc.append_module_event(_module_event())

    content = writer.read("nex-test", "HISTORY.md")
    assert "# nex-test — History" in content
    assert "Module MM created — Manažér modulov (Systém)" in content


def test_append_module_event_dedup_on_replay(tmp_path: Path) -> None:
    """Writer-level dedup keeps a replay of the same event idempotent."""
    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("nex-test", writer=writer)
    event = _module_event()

    svc.append_module_event(event)
    svc.append_module_event(event)

    content = writer.read("nex-test", "HISTORY.md")
    assert content.count("Module MM created") == 1


def test_append_module_event_no_writer_is_noop() -> None:
    svc = LiveDocumentService("nex-test")  # writer=None
    svc.append_module_event(_module_event())  # must not raise


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


# ── DB factory helpers (for generate_status_md) ──────────────────────


def _make_user(db_session: Any) -> User:
    user = User(
        username=f"user_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed_password_placeholder",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session: Any, *, name: str | None = None, slug: str | None = None) -> Project:
    user = _make_user(db_session)
    suffix = uuid.uuid4().hex[:8]
    project = Project(
        name=name or f"Project {suffix}",
        slug=slug or f"project-{suffix}",
        category="multimodule",
        description="Test project",
        created_by=user.id,
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_version(
    db_session: Any,
    *,
    project: Project,
    version_number: str = "v1.0",
    name: str = "Foundation",
) -> Version:
    version = Version(
        project_id=project.id,
        version_number=version_number,
        name=name,
    )
    db_session.add(version)
    db_session.flush()
    return version


def _make_epic(
    db_session: Any,
    *,
    project: Project,
    number: int | None = None,
    title: str = "Epic",
    status: str = "planned",
    version: Version | None = None,
) -> Epic:
    if number is None:
        current = db_session.execute(
            sa_select(Epic.number)
            .where(Epic.project_id == project.id)
            .order_by(Epic.number.desc())
            .limit(1)
        ).scalar()
        number = (current or 0) + 1
    epic = Epic(
        project_id=project.id,
        number=number,
        title=title,
        status=status,
        version_id=version.id if version else None,
    )
    db_session.add(epic)
    db_session.flush()
    return epic


def _make_feat(
    db_session: Any,
    *,
    epic: Epic,
    number: int | None = None,
    title: str = "Feat",
    status: str = "todo",
) -> Feat:
    if number is None:
        current = db_session.execute(
            sa_select(Feat.number)
            .where(Feat.epic_id == epic.id)
            .order_by(Feat.number.desc())
            .limit(1)
        ).scalar()
        number = (current or 0) + 1
    feat = Feat(
        epic_id=epic.id,
        number=number,
        title=title,
        status=status,
    )
    db_session.add(feat)
    db_session.flush()
    return feat


def _make_task(
    db_session: Any,
    *,
    feat: Feat,
    number: int | None = None,
    title: str = "Task",
    status: str = "todo",
    task_type: str = "backend",
) -> Task:
    if number is None:
        current = db_session.execute(
            sa_select(Task.number)
            .where(Task.feat_id == feat.id)
            .order_by(Task.number.desc())
            .limit(1)
        ).scalar()
        number = (current or 0) + 1
    task = Task(
        feat_id=feat.id,
        number=number,
        title=title,
        task_type=task_type,
        status=status,
    )
    db_session.add(task)
    db_session.flush()
    return task


def _make_execution_log(
    db_session: Any,
    *,
    task: Task,
    commit_hash: str | None,
    status: str = "done",
    created_at: datetime | None = None,
) -> ExecutionLog:
    delegation = Delegation(
        task_id=task.id,
        prompt=f"delegation-{uuid.uuid4().hex[:6]}",
    )
    db_session.add(delegation)
    db_session.flush()
    kwargs: dict[str, Any] = {
        "delegation_id": delegation.id,
        "task_id": task.id,
        "status": status,
        "commit_hash": commit_hash,
    }
    # Explicit ``created_at`` lets tests order rows deterministically;
    # without it, three logs flushed in quick succession can share the
    # same ``now()`` tick and the ORDER BY falls back to insertion order.
    if created_at is not None:
        kwargs["created_at"] = created_at
    log = ExecutionLog(**kwargs)
    db_session.add(log)
    db_session.flush()
    return log


# ── generate_status_md — DB-backed ───────────────────────────────────


def test_status_md_project_not_found(db_session: Any) -> None:
    svc = LiveDocumentService("does-not-matter")

    fake_id = uuid.uuid4()
    md = svc.generate_status_md(db_session, fake_id)

    assert md == "# Unknown Project — Status\n\nProject not found.\n"


def test_status_md_empty_project(db_session: Any) -> None:
    project = _make_project(db_session, name="My App", slug="my-app")
    svc = LiveDocumentService(project.slug)

    md = svc.generate_status_md(db_session, project.id)

    assert "# My App — Status" in md
    assert "Updated: " in md
    assert "No epics planned yet." in md


def test_status_md_basic_hierarchy(db_session: Any) -> None:
    project = _make_project(db_session, name="App", slug="app")
    epic = _make_epic(
        db_session, project=project, number=1, title="Foundation", status="in_progress"
    )
    feat = _make_feat(db_session, epic=epic, number=1, title="Auth", status="in_progress")
    _make_task(db_session, feat=feat, number=1, title="Login endpoint", status="done")
    _make_task(db_session, feat=feat, number=2, title="Logout endpoint", status="todo")

    svc = LiveDocumentService(project.slug)
    md = svc.generate_status_md(db_session, project.id)

    assert "## Epic 1: Foundation — IN PROGRESS" in md
    assert "### Feat 1.1: Auth — IN PROGRESS" in md
    assert "- [x] 1.1.1 Login endpoint" in md
    assert "- [ ] 1.1.2 Logout endpoint" in md
    # Summary line
    assert "Epics: 0/1" in md
    assert "Feats: 0/1" in md
    assert "Tasks: 1/2" in md


def test_status_md_version_renders_in_epic_header(db_session: Any) -> None:
    project = _make_project(db_session)
    version = _make_version(db_session, project=project, version_number="v1.0", name="F")
    _make_epic(db_session, project=project, number=1, title="E", version=version)

    svc = LiveDocumentService(project.slug)
    md = svc.generate_status_md(db_session, project.id)

    assert "## Epic 1: E — PLANNED  [v1.0]" in md


def test_status_md_epic_without_version_has_no_bracket(db_session: Any) -> None:
    project = _make_project(db_session)
    _make_epic(db_session, project=project, number=1, title="E")

    svc = LiveDocumentService(project.slug)
    md = svc.generate_status_md(db_session, project.id)

    assert "## Epic 1: E — PLANNED" in md
    assert "[v" not in md  # no version bracket anywhere


def test_status_md_commit_hash_trimmed_to_seven(db_session: Any) -> None:
    project = _make_project(db_session)
    epic = _make_epic(db_session, project=project, number=1)
    feat = _make_feat(db_session, epic=epic, number=1)
    task = _make_task(db_session, feat=feat, number=1, title="T", status="done")
    _make_execution_log(db_session, task=task, commit_hash="b8fa302deadbeef1234")

    svc = LiveDocumentService(project.slug)
    md = svc.generate_status_md(db_session, project.id)

    assert "- [x] 1.1.1 T (b8fa302)" in md
    assert "deadbeef" not in md


def test_status_md_done_task_without_execution_log_has_no_commit(db_session: Any) -> None:
    project = _make_project(db_session)
    epic = _make_epic(db_session, project=project, number=1)
    feat = _make_feat(db_session, epic=epic, number=1)
    _make_task(db_session, feat=feat, number=1, title="T", status="done")

    svc = LiveDocumentService(project.slug)
    md = svc.generate_status_md(db_session, project.id)

    assert "- [x] 1.1.1 T" in md
    assert "- [x] 1.1.1 T (" not in md  # no parenthesised commit


def test_status_md_newest_execution_log_wins(db_session: Any) -> None:
    project = _make_project(db_session)
    epic = _make_epic(db_session, project=project, number=1)
    feat = _make_feat(db_session, epic=epic, number=1)
    task = _make_task(db_session, feat=feat, number=1, title="T", status="done")

    # Three logs with explicit, monotonically-increasing timestamps —
    # newest should win via ORDER BY created_at DESC.
    _make_execution_log(
        db_session,
        task=task,
        commit_hash="aaaaaaa1111",
        created_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
    )
    _make_execution_log(
        db_session,
        task=task,
        commit_hash="bbbbbbb2222",
        created_at=datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc),
    )
    _make_execution_log(
        db_session,
        task=task,
        commit_hash="ccccccc3333",
        created_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
    )

    svc = LiveDocumentService(project.slug)
    md = svc.generate_status_md(db_session, project.id)

    assert "(ccccccc)" in md
    assert "(aaaaaaa)" not in md
    assert "(bbbbbbb)" not in md


def test_status_md_ignores_execution_logs_without_commit(db_session: Any) -> None:
    project = _make_project(db_session)
    epic = _make_epic(db_session, project=project, number=1)
    feat = _make_feat(db_session, epic=epic, number=1)
    task = _make_task(db_session, feat=feat, number=1, title="T", status="done")

    # An older log with a commit + a newer log with NULL commit — the real
    # commit should still surface (NULL rows are filtered out in the query).
    _make_execution_log(db_session, task=task, commit_hash="feedface1234")
    _make_execution_log(db_session, task=task, commit_hash=None)

    svc = LiveDocumentService(project.slug)
    md = svc.generate_status_md(db_session, project.id)

    assert "(feedfac)" in md


def test_status_md_hierarchical_numbering_across_epics(db_session: Any) -> None:
    project = _make_project(db_session)

    epic1 = _make_epic(db_session, project=project, number=1, title="E1")
    feat11 = _make_feat(db_session, epic=epic1, number=1, title="F1")
    _make_task(db_session, feat=feat11, number=1, title="T11a")
    _make_task(db_session, feat=feat11, number=2, title="T11b")
    feat12 = _make_feat(db_session, epic=epic1, number=2, title="F2")
    _make_task(db_session, feat=feat12, number=1, title="T12a")

    epic2 = _make_epic(db_session, project=project, number=2, title="E2")
    feat21 = _make_feat(db_session, epic=epic2, number=1, title="F3")
    _make_task(db_session, feat=feat21, number=1, title="T21a")

    svc = LiveDocumentService(project.slug)
    md = svc.generate_status_md(db_session, project.id)

    # Explicit hierarchical numbering in task list lines.
    assert "- [ ] 1.1.1 T11a" in md
    assert "- [ ] 1.1.2 T11b" in md
    assert "- [ ] 1.2.1 T12a" in md
    assert "- [ ] 2.1.1 T21a" in md

    # Ordering: epic 1 comes before epic 2 in the rendered output.
    assert md.index("Epic 1") < md.index("Epic 2")


def test_status_md_summary_counts_mixed_statuses(db_session: Any) -> None:
    project = _make_project(db_session)

    epic_done = _make_epic(db_session, project=project, number=1, status="done")
    feat_done = _make_feat(db_session, epic=epic_done, number=1, status="done")
    _make_task(db_session, feat=feat_done, number=1, status="done")
    _make_task(db_session, feat=feat_done, number=2, status="done")

    epic_ip = _make_epic(db_session, project=project, number=2, status="in_progress")
    feat_ip = _make_feat(db_session, epic=epic_ip, number=1, status="in_progress")
    _make_task(db_session, feat=feat_ip, number=1, status="done")
    _make_task(db_session, feat=feat_ip, number=2, status="todo")
    _make_task(db_session, feat=feat_ip, number=3, status="failed")

    svc = LiveDocumentService(project.slug)
    md = svc.generate_status_md(db_session, project.id)

    # 1/2 epics done, 1/2 feats done, 3/5 tasks done.
    assert "Epics: 1/2" in md
    assert "Feats: 1/2" in md
    assert "Tasks: 3/5" in md


def test_status_md_feat_without_tasks_still_renders(db_session: Any) -> None:
    project = _make_project(db_session)
    epic = _make_epic(db_session, project=project, number=1)
    _make_feat(db_session, epic=epic, number=1, title="Planned feat", status="todo")

    svc = LiveDocumentService(project.slug)
    md = svc.generate_status_md(db_session, project.id)

    assert "### Feat 1.1: Planned feat — TODO" in md
    assert "Tasks: 0/0" in md


# ── regenerate_status — persistence ──────────────────────────────────


def test_regenerate_status_saves_to_kb(db_session: Any, tmp_path: Path) -> None:
    project = _make_project(db_session, slug="app")
    epic = _make_epic(db_session, project=project, number=1, title="E")
    feat = _make_feat(db_session, epic=epic, number=1, title="F")
    _make_task(db_session, feat=feat, number=1, title="T", status="done")

    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("app", writer=writer)

    svc.regenerate_status(db_session, project.id)

    content = writer.read("app", "STATUS.md")
    assert "## Epic 1: E" in content
    assert "- [x] 1.1.1 T" in content


def test_regenerate_status_overwrites_previous(db_session: Any, tmp_path: Path) -> None:
    project = _make_project(db_session, slug="app")
    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("app", writer=writer)

    # Pre-seed STATUS.md with stale content.
    writer.save("app", "STATUS.md", "stale content from before\n")

    svc.regenerate_status(db_session, project.id)

    content = writer.read("app", "STATUS.md")
    assert "stale content from before" not in content
    assert "# " in content  # starts with project header


def test_regenerate_status_no_writer_is_noop(db_session: Any) -> None:
    project = _make_project(db_session, slug="app")
    svc = LiveDocumentService("app")  # writer=None

    # Must not raise even though no writer is configured.
    svc.regenerate_status(db_session, project.id)


# ── init_live_documents — project creation seed ──────────────────────


def test_init_live_documents_creates_three_files(
    db_session: Any, tmp_path: Path
) -> None:
    project = _make_project(db_session, slug="init-test")
    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("init-test", writer=writer)

    svc.init_live_documents(db_session, project.id)

    assert writer.exists("init-test", "STATUS.md") is True
    assert writer.exists("init-test", "HISTORY.md") is True
    assert writer.exists("init-test", "ARCHITECT.md") is True


def test_init_live_documents_status_shows_empty_state(
    db_session: Any, tmp_path: Path
) -> None:
    project = _make_project(db_session, name="Empty", slug="empty")
    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("empty", writer=writer)

    svc.init_live_documents(db_session, project.id)

    status_md = writer.read("empty", "STATUS.md")
    assert "# Empty — Status" in status_md
    assert "No epics planned yet." in status_md


def test_init_live_documents_history_is_header_only(
    db_session: Any, tmp_path: Path
) -> None:
    project = _make_project(db_session, slug="hist-test")
    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("hist-test", writer=writer)

    svc.init_live_documents(db_session, project.id)

    assert writer.read("hist-test", "HISTORY.md") == "# hist-test — History\n\n"


def test_init_live_documents_architect_is_header_only(
    db_session: Any, tmp_path: Path
) -> None:
    project = _make_project(db_session, slug="arch-test")
    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("arch-test", writer=writer)

    svc.init_live_documents(db_session, project.id)

    assert writer.read("arch-test", "ARCHITECT.md") == (
        "# arch-test — Architecture Log\n\n"
    )


def test_init_live_documents_raises_without_writer(db_session: Any) -> None:
    project = _make_project(db_session, slug="no-writer")
    svc = LiveDocumentService("no-writer")  # writer=None

    with pytest.raises(RuntimeError, match="requires a KnowledgeBaseWriter"):
        svc.init_live_documents(db_session, project.id)


def test_init_live_documents_overwrites_existing_files(
    db_session: Any, tmp_path: Path
) -> None:
    """Re-running init on an existing KB cleanly overwrites any stale content."""
    project = _make_project(db_session, slug="redo")
    writer = KnowledgeBaseWriter(tmp_path)
    svc = LiveDocumentService("redo", writer=writer)

    # Pre-seed STATUS.md with stale content a previous init might have left.
    writer.save("redo", "STATUS.md", "stale content\n")
    writer.save("redo", "HISTORY.md", "stale history\n")
    writer.save("redo", "ARCHITECT.md", "stale architect\n")

    svc.init_live_documents(db_session, project.id)

    assert "stale content" not in writer.read("redo", "STATUS.md")
    assert writer.read("redo", "HISTORY.md") == "# redo — History\n\n"
    assert writer.read("redo", "ARCHITECT.md") == "# redo — Architecture Log\n\n"

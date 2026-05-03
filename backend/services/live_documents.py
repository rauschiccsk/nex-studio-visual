"""Live document service — deterministic markdown generators and
persistence for per-project ``STATUS.md`` / ``ARCHITECT.md`` /
``HISTORY.md``.

Ported from NEX Command (``backend/services/live_documents.py``, see
``docs/architect/live-docs-port.md``).

``HISTORY.md`` / ``ARCHITECT.md`` are append-only with a first-line
dedup guard provided by :class:`KnowledgeBaseWriter` — replaying the
same task completion is idempotent. ``STATUS.md`` is a full rebuild
(``save``, overwrite) from the current ``Project → Version → Epic →
Feat → Task`` tree; :meth:`LiveDocumentService.generate_status_md`
produces the markdown and :meth:`regenerate_status` persists it.

The service keeps a thin invariant: generators for history /
architect entries are pure functions of their input data; the STATUS
generator is a DB-driven rebuild parameterised on ``(db, project_id)``.
Persistence methods (``append_*`` / ``regenerate_status``) layer the
writer on top. Pass ``writer=None`` to get string generation only
(useful in tests and in call sites that want to preview an entry
before commit).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.delegations import ExecutionLog
from backend.db.models.projects import Project, ProjectModule
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.schemas.live_documents import (
    FeatCompletionData,
    ModuleEventData,
    TaskCompletionData,
)
from backend.services.knowledge_base_writer import KnowledgeBaseWriter

# Architecture-relevant file patterns — a changed file lands in
# ``ARCHITECT.md`` when it has one of these extensions or basenames
# and is not inside a skip-pattern (tests, caches, node_modules).
_ARCH_EXTENSIONS: tuple[str, ...] = (
    ".py",
    ".ts",
    ".tsx",
    ".sql",
    ".yml",
    ".yaml",
    ".toml",
    ".cfg",
    ".sh",
)
_ARCH_BASENAMES: tuple[str, ...] = (
    "Dockerfile",
    "Makefile",
    "docker-compose.yml",
    "docker-compose.yaml",
)
_SKIP_PATTERNS: tuple[str, ...] = (
    "__pycache__",
    ".pyc",
    "node_modules",
    ".test.",
    "_test.",
    "test_",
)


class LiveDocumentService:
    """Per-project façade over the markdown generators and the KB writer.

    Instantiate with the project's slug and an optional
    :class:`KnowledgeBaseWriter`. Without a writer the service is
    pure string generation — useful for previewing entries or for
    testing generators in isolation. With a writer, the
    ``append_history`` / ``append_architect`` / ``append_phase_summary``
    methods persist the generated entry under
    ``projects/{slug}/{FILE}.md``.
    """

    def __init__(
        self,
        project_slug: str,
        writer: KnowledgeBaseWriter | None = None,
    ) -> None:
        self._slug = project_slug
        self._writer = writer

    # ── generators ────────────────────────────────────────────────────

    def generate_history_entry(self, data: TaskCompletionData) -> str:
        """Return the two-line ``HISTORY.md`` entry for a task completion.

        Format:

            HH:MM Task F.T {icon} — {title} ({duration}s[, commit7])
              Code Review: {PASS|FAIL} | Audit: {PASS|FAIL} ({Nth attempt})
        """
        ts = data.timestamp.strftime("%H:%M")
        status_icon = "✅" if data.status == "done" else "❌"

        commit_suffix = ""
        if data.commit_hashes:
            commit_suffix = f", {data.commit_hashes[0][:7]}"

        review = "PASS" if data.code_review_passed else "FAIL"
        audit = "PASS" if data.audit_passed else "FAIL"
        attempt = _ordinal(data.auto_fix_attempts + 1) + " attempt"

        line1 = (
            f"{ts} Task {data.feat_number}.{data.task_number} "
            f"{status_icon} — {data.task_title} "
            f"({data.duration_seconds:.1f}s{commit_suffix})"
        )
        line2 = f"  Code Review: {review} | Audit: {audit} ({attempt})"
        return f"{line1}\n{line2}\n"

    def generate_architect_entry(self, data: TaskCompletionData) -> str:
        """Return the ``ARCHITECT.md`` entry for a task completion.

        Format:

            ### Task F.T: {title}
            Files: {a, b, c}           ← only if arch-relevant files present
            Commits: {h1, h2, …}       ← only if commits present

        Returns empty string when the task failed **and** produced no
        commits — there is nothing meaningful to record. A failed task
        that still committed (e.g. partial progress before fail) keeps
        the commit trail.
        """
        if not data.commit_hashes and data.status != "done":
            return ""

        lines = [f"### Task {data.feat_number}.{data.task_number}: {data.task_title}"]

        arch_files = _filter_arch_files(data.changed_files)
        if arch_files:
            lines.append(f"Files: {', '.join(arch_files)}")
        if data.commit_hashes:
            lines.append(f"Commits: {', '.join(data.commit_hashes)}")
        lines.append("")
        return "\n".join(lines)

    def generate_status_md(self, db: Session, project_id: UUID) -> str:
        """Rebuild ``STATUS.md`` markdown from the current DB state.

        Queries the ``Project → Version (optional) → Epic → Feat → Task``
        tree plus the latest ``ExecutionLog.commit_hash`` per done task
        and renders a flat hierarchy:

            # {project.name} — Status
            Updated: {YYYY-MM-DD HH:MM UTC}

            ## Epic {n}: {title} — {STATUS}[  [version_number]]
            ### Feat {n}.{m}: {title} — {STATUS}
            - [x] {n}.{m}.{t} {task title} ({commit7})
            - [ ] {n}.{m}.{t+1} {task title}

            ## Summary
            Epics: X/Y | Feats: X/Y | Tasks: X/Y

        Version appears as a bracketed suffix on the Epic header when
        ``epic.version_id`` is set; version-less epics render without
        it. The 7-character commit suffix appears only on done tasks
        that have at least one ``ExecutionLog`` row with a non-null
        ``commit_hash`` — if a task has several such logs, the newest
        one wins.

        Returns a special message when the project does not exist
        (mirrors NEX Command behaviour) so the generator is safe to
        call even during clean-up flows.
        """
        project = db.get(Project, project_id)
        if project is None:
            return "# Unknown Project — Status\n\nProject not found.\n"

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Modules section — only rendered for multi-module projects.
        # A single-module project has at most one module and does not
        # benefit from a dedicated list.
        modules: list[ProjectModule] = []
        if project.category == "multimodule":
            modules = list(
                db.execute(
                    select(ProjectModule).where(ProjectModule.project_id == project_id).order_by(ProjectModule.code)
                ).scalars()
            )

        epics_rows = list(
            db.execute(
                select(Epic, Version)
                .join(Version, Epic.version_id == Version.id, isouter=True)
                .where(Epic.project_id == project_id)
                .order_by(Epic.number)
            ).all()
        )

        # Short-circuit "empty project" render — but only when there are
        # also no modules. Multi-module projects with modules but no
        # epics yet still deserve the modules section.
        if not epics_rows and not modules:
            return f"# {project.name} — Status\nUpdated: {now}\n\nNo epics planned yet.\n"

        epic_ids = [epic.id for epic, _ in epics_rows]
        feats_by_epic = _group_feats_by_epic(db, epic_ids)
        feat_ids = [f.id for feats in feats_by_epic.values() for f in feats]
        tasks_by_feat = _group_tasks_by_feat(db, feat_ids)
        done_task_ids = [t.id for feat_tasks in tasks_by_feat.values() for t in feat_tasks if t.status == "done"]
        commit_by_task = _latest_commit_per_task(db, done_task_ids)

        lines: list[str] = [f"# {project.name} — Status", f"Updated: {now}", ""]

        # Modules (multi-module projects only).
        modules_done = sum(1 for m in modules if m.status == "done")
        if modules:
            lines.append(f"## Modules ({len(modules)})")
            for m in modules:
                lines.append(f"- [{m.status}] {m.code} · {m.name} · {m.category}")
            lines.append("")
        elif project.category == "multimodule":
            # Multi-module project with no modules yet — leave an explicit
            # heading so the STATUS isn't misleadingly "empty".
            lines.append("## Modules (0)")
            lines.append("No modules planned yet.")
            lines.append("")

        epics_done = 0
        feats_total = 0
        feats_done = 0
        tasks_total = 0
        tasks_done = 0

        for epic, version in epics_rows:
            if epic.status == "done":
                epics_done += 1

            header = f"## Epic {epic.number}: {epic.title} — {epic.status.upper().replace('_', ' ')}"
            if version is not None:
                header += f"  [{version.version_number}]"
            lines.append(header)

            epic_feats = feats_by_epic.get(epic.id, [])
            feats_total += len(epic_feats)

            for feat in epic_feats:
                if feat.status == "done":
                    feats_done += 1

                lines.append(
                    f"### Feat {epic.number}.{feat.number}: {feat.title} — {feat.status.upper().replace('_', ' ')}"
                )

                feat_tasks = tasks_by_feat.get(feat.id, [])
                tasks_total += len(feat_tasks)

                for task in feat_tasks:
                    if task.status == "done":
                        tasks_done += 1
                    checkbox = "[x]" if task.status == "done" else "[ ]"
                    commit = commit_by_task.get(task.id)
                    commit_suffix = f" ({commit[:7]})" if commit else ""
                    label = f"{epic.number}.{feat.number}.{task.number}"
                    lines.append(f"- {checkbox} {label} {task.title}{commit_suffix}")

                lines.append("")

        lines.append("## Summary")
        summary_parts = []
        if project.category == "multimodule":
            summary_parts.append(f"Modules: {modules_done}/{len(modules)} done")
        summary_parts.append(f"Epics: {epics_done}/{len(epics_rows)}")
        summary_parts.append(f"Feats: {feats_done}/{feats_total}")
        summary_parts.append(f"Tasks: {tasks_done}/{tasks_total}")
        lines.append(" | ".join(summary_parts))
        lines.append("")

        return "\n".join(lines)

    def generate_module_event_entry(self, data: ModuleEventData) -> str:
        """Return a single ``HISTORY.md`` line describing a module event.

        Format matches the task-completion / phase-summary entries —
        ``HH:MM`` prefix, single verb-form sentence:

            HH:MM Module MM created — Manažér modulov (Systém)
            HH:MM Module MM status planned → in_development
            HH:MM Module MM deleted — Manažér modulov
        """
        ts = data.timestamp.strftime("%H:%M")
        code = data.module_code
        if data.event_type == "created":
            return f"{ts} Module {code} created — {data.module_name} ({data.category})\n"
        if data.event_type == "deleted":
            return f"{ts} Module {code} deleted — {data.module_name}\n"
        # status_changed
        return f"{ts} Module {code} status {data.old_status} → {data.new_status}\n"

    def generate_phase_summary_entry(self, data: FeatCompletionData) -> str:
        """Return the phase-closing entry appended to ``HISTORY.md``.

        Format:

            HH:MM Feat N COMPLETE — {title}
              Tasks: {N} | Duration: {hMmS} | Audit: {PASS|FAIL|NA} | CI: {GREEN|RED|N/A}
            {50 equals signs}
        """
        ts = data.timestamp.strftime("%H:%M")
        audit = data.audit_result.upper()  # pass/fail/na → PASS/FAIL/NA
        ci = {"pass": "GREEN", "fail": "RED", "na": "N/A"}[data.ci_result]
        duration = _format_duration(data.duration_seconds)

        return (
            f"{ts} Feat {data.feat_number} COMPLETE — {data.feat_title}\n"
            f"  Tasks: {data.total_tasks} | Duration: {duration} | "
            f"Audit: {audit} | CI: {ci}\n"
            f"{'=' * 50}\n"
        )

    # ── persistence ───────────────────────────────────────────────────

    def append_history(self, data: TaskCompletionData) -> None:
        """Persist a task completion entry to ``HISTORY.md``.

        No-op when the writer is not configured (pure-generation mode).
        """
        entry = self.generate_history_entry(data)
        if not entry or self._writer is None:
            return
        self._writer.append(
            self._slug,
            "HISTORY.md",
            entry,
            header_if_new=self._history_header(),
        )

    def append_architect(self, data: TaskCompletionData) -> None:
        """Persist a task completion entry to ``ARCHITECT.md``.

        Skipped when the generator returns empty (failed task without
        commits) or when the writer is not configured.
        """
        entry = self.generate_architect_entry(data)
        if not entry or self._writer is None:
            return
        self._writer.append(
            self._slug,
            "ARCHITECT.md",
            entry,
            header_if_new=self._architect_header(),
        )

    def regenerate_status(self, db: Session, project_id: UUID) -> None:
        """Rebuild ``STATUS.md`` from the DB and overwrite it in the KB.

        Uses :meth:`KnowledgeBaseWriter.save` (overwrite), not append —
        ``STATUS.md`` reflects the current DB state as a whole, so
        patching is incorrect. No-op when the writer is not configured.
        """
        if self._writer is None:
            return
        content = self.generate_status_md(db, project_id)
        self._writer.save(self._slug, "STATUS.md", content)

    def init_live_documents(self, db: Session, project_id: UUID) -> None:
        """Seed the three live documents for a freshly created project.

        Writes ``STATUS.md`` (generated from the then-current DB state —
        typically "no epics planned yet" right after creation),
        ``HISTORY.md`` (header only) and ``ARCHITECT.md`` (header only)
        under ``projects/{slug}/``. Uses
        :meth:`KnowledgeBaseWriter.save` (overwrite) for all three so
        the operation is idempotent across crash-restart scenarios.

        Unlike the other persistence wrappers this method **requires**
        a writer — the caller explicitly asked to persist. Raises
        :class:`RuntimeError` if the service was constructed without
        one, rather than silently no-op'ing; the router catches I/O
        failures as ``OSError`` and translates them into a 500.
        """
        if self._writer is None:
            raise RuntimeError(
                "init_live_documents requires a KnowledgeBaseWriter; none was configured on the service."
            )
        status_md = self.generate_status_md(db, project_id)
        self._writer.save(self._slug, "STATUS.md", status_md)
        self._writer.save(self._slug, "HISTORY.md", self._history_header())
        self._writer.save(self._slug, "ARCHITECT.md", self._architect_header())

    def append_module_event(self, data: ModuleEventData) -> None:
        """Persist a module-lifecycle entry to ``HISTORY.md``.

        No-op when the writer is not configured.
        """
        entry = self.generate_module_event_entry(data)
        if not entry or self._writer is None:
            return
        self._writer.append(
            self._slug,
            "HISTORY.md",
            entry,
            header_if_new=self._history_header(),
        )

    def append_phase_summary(self, data: FeatCompletionData) -> None:
        """Append the feat-completion summary entry to ``HISTORY.md``.

        No-op when the writer is not configured.
        """
        entry = self.generate_phase_summary_entry(data)
        if not entry or self._writer is None:
            return
        self._writer.append(
            self._slug,
            "HISTORY.md",
            entry,
            header_if_new=self._history_header(),
        )

    # ── headers ───────────────────────────────────────────────────────

    def _history_header(self) -> str:
        return f"# {self._slug} — History\n\n"

    def _architect_header(self) -> str:
        return f"# {self._slug} — Architecture Log\n\n"


# ── module-level helpers ─────────────────────────────────────────────


def _group_feats_by_epic(db: Session, epic_ids: list[UUID]) -> dict[UUID, list[Feat]]:
    """Return feats grouped by ``epic_id``, each list ordered by ``number ASC``."""
    if not epic_ids:
        return {}
    feats = db.execute(select(Feat).where(Feat.epic_id.in_(epic_ids)).order_by(Feat.number)).scalars()
    grouped: dict[UUID, list[Feat]] = {}
    for feat in feats:
        grouped.setdefault(feat.epic_id, []).append(feat)
    return grouped


def _group_tasks_by_feat(db: Session, feat_ids: list[UUID]) -> dict[UUID, list[Task]]:
    """Return tasks grouped by ``feat_id``, each list ordered by ``number ASC``."""
    if not feat_ids:
        return {}
    tasks = db.execute(select(Task).where(Task.feat_id.in_(feat_ids)).order_by(Task.number)).scalars()
    grouped: dict[UUID, list[Task]] = {}
    for task in tasks:
        grouped.setdefault(task.feat_id, []).append(task)
    return grouped


def _latest_commit_per_task(db: Session, task_ids: list[UUID]) -> dict[UUID, str]:
    """Return a mapping of ``task_id`` → newest ``ExecutionLog.commit_hash``.

    Only rows with ``status='done'`` and a non-null ``commit_hash`` are
    considered; for a task with multiple such rows, the newest one
    (``created_at DESC``) wins.
    """
    if not task_ids:
        return {}
    rows = db.execute(
        select(ExecutionLog.task_id, ExecutionLog.commit_hash)
        .where(
            ExecutionLog.task_id.in_(task_ids),
            ExecutionLog.commit_hash.is_not(None),
            ExecutionLog.status == "done",
        )
        .order_by(ExecutionLog.task_id, ExecutionLog.created_at.desc())
    ).all()
    commit_by_task: dict[UUID, str] = {}
    for task_id, commit_hash in rows:
        # First row per task_id due to DESC order — setdefault preserves it.
        commit_by_task.setdefault(task_id, commit_hash)
    return commit_by_task


def _filter_arch_files(files: list[str]) -> list[str]:
    """Filter changed files down to architecture-relevant ones.

    Keeps files with :data:`_ARCH_EXTENSIONS` extensions and the
    literal :data:`_ARCH_BASENAMES` (``Dockerfile`` etc.); drops
    markdown, tests, caches, ``node_modules`` and anything else.
    Order is preserved.
    """
    result: list[str] = []
    for f in files:
        if any(skip in f for skip in _SKIP_PATTERNS):
            continue
        if f.endswith(".md"):
            continue
        basename = f.rsplit("/", 1)[-1] if "/" in f else f
        if basename in _ARCH_BASENAMES:
            result.append(f)
            continue
        if any(f.endswith(ext) for ext in _ARCH_EXTENSIONS):
            result.append(f)
    return result


def _ordinal(n: int) -> str:
    """Return the English ordinal string for ``n`` (1st, 2nd, 3rd, 4th, …)."""
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _format_duration(seconds: float) -> str:
    """Format a duration in a coarse, human-readable form.

    Under a minute: ``Ns``. Under an hour: ``MmSs``. Otherwise:
    ``HhMm``. Sub-second precision is dropped — live docs are a
    narrative log, not a profiler.
    """
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m{secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h{mins}m"

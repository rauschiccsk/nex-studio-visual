"""Live document service — deterministic markdown generators and
persistence for per-project ``STATUS.md`` / ``HISTORY.md``.

Ported from NEX Command (``backend/services/live_documents.py``, see
``docs/architect/live-docs-port.md``).

``HISTORY.md`` is append-only with a first-line dedup guard provided
by :class:`KnowledgeBaseWriter` — replaying the same task completion
is idempotent. ``STATUS.md`` is a full rebuild (``save``, overwrite)
from the current ``Project → Version → Epic → Feat → Task`` tree;
:meth:`LiveDocumentService.generate_status_md` produces the markdown
and :meth:`regenerate_status` persists it.

The service keeps a thin invariant: generators for history entries
are pure functions of their input data; the STATUS generator is a
DB-driven rebuild parameterised on ``(db, project_id)``. Persistence
methods (``append_*`` / ``regenerate_status``) layer the writer on top.
Pass ``writer=None`` to get string generation only (useful in tests and
in call sites that want to preview an entry before commit).

ARCHITECT.md was deprecated as part of the three-agent architecture
migration (Designer/Implementer/Auditor) — per-agent session logs in
``docs/session-logs/<role>/`` replace it with granular, attributable
records. Existing ARCHITECT.md files in the KB remain as historical
artefacts but receive no new writes from this service.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.schemas.live_documents import (
    FeatCompletionData,
    TaskCompletionData,
)
from backend.services.knowledge_base_writer import KnowledgeBaseWriter

if TYPE_CHECKING:
    from backend.rag.indexer import RAGIndexer

logger = logging.getLogger(__name__)

#: Qdrant tenant collection the per-project KB lives in. The live documents are
#: written under ``{knowledge_base_path}/projects/{slug}/`` (the ``/home/icc/knowledge``
#: tree) → the ``icc`` collection, matching the ``/knowledge`` routes' default tenant.
_KB_TENANT = "icc"


class LiveDocumentService:
    """Per-project façade over the markdown generators and the KB writer.

    Instantiate with the project's slug and an optional
    :class:`KnowledgeBaseWriter`. Without a writer the service is
    pure string generation — useful for previewing entries or for
    testing generators in isolation. With a writer, the
    ``append_history`` / ``append_phase_summary`` methods persist
    the generated entry under ``projects/{slug}/{FILE}.md``.

    An optional :class:`RAGIndexer` (``indexer``) keeps the RAG vector store in
    sync: every persisted ``STATUS.md`` / ``HISTORY.md`` write triggers a
    reindex of that file (CLAUDE.md §13 — "žiadna KB zmena bez následného
    reindexu"). Like ``writer`` it is optional — without it, writes persist to
    disk only (the pre-existing behaviour, used by pure-generation tests and any
    call site that does not wire RAG). A reindex failure is logged and swallowed
    so it never fails the write path (mirrors the ``/knowledge`` routes).
    """

    def __init__(
        self,
        project_slug: str,
        writer: KnowledgeBaseWriter | None = None,
        indexer: "RAGIndexer | None" = None,
    ) -> None:
        self._slug = project_slug
        self._writer = writer
        self._indexer = indexer

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
        it.

        Returns a special message when the project does not exist
        (mirrors NEX Command behaviour) so the generator is safe to
        call even during clean-up flows.
        """
        project = db.get(Project, project_id)
        if project is None:
            return "# Unknown Project — Status\n\nProject not found.\n"

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        epics_rows = list(
            db.execute(
                select(Epic, Version)
                .join(Version, Epic.version_id == Version.id, isouter=True)
                .where(Epic.project_id == project_id)
                .order_by(Epic.number)
            ).all()
        )

        # Short-circuit "empty project" render.
        if not epics_rows:
            return f"# {project.name} — Status\nUpdated: {now}\n\nNo epics planned yet.\n"

        epic_ids = [epic.id for epic, _ in epics_rows]
        feats_by_epic = _group_feats_by_epic(db, epic_ids)
        feat_ids = [f.id for feats in feats_by_epic.values() for f in feats]
        tasks_by_feat = _group_tasks_by_feat(db, feat_ids)
        # Commit-hash enrichment came from the removed ExecutionLog
        # delegation pipeline (CR-NS-008); done tasks now render without
        # a commit suffix.
        commit_by_task: dict[UUID, str] = {}

        lines: list[str] = [f"# {project.name} — Status", f"Updated: {now}", ""]

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
        summary_parts.append(f"Epics: {epics_done}/{len(epics_rows)}")
        summary_parts.append(f"Feats: {feats_done}/{feats_total}")
        summary_parts.append(f"Tasks: {tasks_done}/{tasks_total}")
        lines.append(" | ".join(summary_parts))
        lines.append("")

        return "\n".join(lines)

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
        self._reindex("HISTORY.md")

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
        self._reindex("STATUS.md")

    def init_live_documents(self, db: Session, project_id: UUID) -> None:
        """Seed the two live documents for a freshly created project.

        Writes ``STATUS.md`` (generated from the then-current DB state —
        typically "no epics planned yet" right after creation) and
        ``HISTORY.md`` (header only) under ``projects/{slug}/``. Uses
        :meth:`KnowledgeBaseWriter.save` (overwrite) for both so the
        operation is idempotent across crash-restart scenarios.

        Unlike the other persistence wrappers this method **requires**
        a writer — the caller explicitly asked to persist. Raises
        :class:`RuntimeError` if the service was constructed without
        one, rather than silently no-op'ing; the router catches I/O
        failures as ``OSError`` and translates them into a 500.

        ARCHITECT.md is no longer seeded — see module docstring for the
        three-agent migration context.
        """
        if self._writer is None:
            raise RuntimeError(
                "init_live_documents requires a KnowledgeBaseWriter; none was configured on the service."
            )
        status_md = self.generate_status_md(db, project_id)
        self._writer.save(self._slug, "STATUS.md", status_md)
        self._reindex("STATUS.md")
        self._writer.save(self._slug, "HISTORY.md", self._history_header())
        self._reindex("HISTORY.md")

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
        self._reindex("HISTORY.md")

    # ── RAG reindex ───────────────────────────────────────────────────

    def _reindex(self, filename: str) -> None:
        """Reindex a just-written live document into the RAG store.

        DRY hook called after every ``STATUS.md`` / ``HISTORY.md`` persist so
        Qdrant tracks the live KB files the same way the ``/knowledge`` routes
        do. Re-reads the current file content (an ``append`` writes only the new
        entry, but the whole document must be reindexed) and upserts it under the
        KB-relative ``projects/{slug}/{filename}`` source id in the ``icc``
        tenant — identical addressing to a ``/knowledge`` write of the same file.

        No-op without an ``indexer`` (or ``writer``). **Graceful on failure**:
        any error (re-read or Qdrant/Ollama) is logged and swallowed so the
        reindex never fails the live-document write (mirrors ``knowledge.py``).

        The enclosing persistence methods run in synchronous request handlers
        (FastAPI runs ``def`` endpoints in a worker thread with no running event
        loop), so ``asyncio.run`` safely drives the async indexer to completion.
        """
        if self._indexer is None or self._writer is None:
            return
        source_file = f"projects/{self._slug}/{filename}"
        try:
            content = self._writer.read(self._slug, filename)
            asyncio.run(
                self._indexer.index_document(
                    file_path=source_file,
                    tenant=_KB_TENANT,
                    content=content,
                )
            )
        except Exception as exc:  # noqa: BLE001 — reindex must never fail the write path
            logger.warning(
                "RAG reindex failed for %s (tenant=%s): %s — live document saved, index may be stale",
                source_file,
                _KB_TENANT,
                exc,
            )

    # ── headers ───────────────────────────────────────────────────────

    def _history_header(self) -> str:
        return f"# {self._slug} — History\n\n"


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

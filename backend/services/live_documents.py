"""Live document service — deterministic markdown generators and
persistence for per-project ``STATUS.md`` / ``ARCHITECT.md`` /
``HISTORY.md``.

Ported from NEX Command (``backend/services/live_documents.py``, see
``docs/architect/live-docs-port.md``). This module covers the
generators and the KB persistence wiring; the STATUS.md rebuild (which
queries the full ``Project → Version → Epic → Feat → Task`` tree) lands
in a follow-up step.

``HISTORY.md`` / ``ARCHITECT.md`` are append-only with a first-line
dedup guard provided by :class:`KnowledgeBaseWriter` — replaying the
same task completion is idempotent. ``STATUS.md`` will be a full
rebuild (``save``, overwrite) to reflect the current DB state.

The service keeps a thin invariant: generators are pure functions of
their input data; persistence methods (``append_*``) layer the writer
on top. Pass ``writer=None`` to get string generation only (useful in
tests and in call sites that want to preview an entry before commit).
"""

from __future__ import annotations

from backend.schemas.live_documents import (
    FeatCompletionData,
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

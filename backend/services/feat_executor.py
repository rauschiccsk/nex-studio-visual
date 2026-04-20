"""Feat execution pipeline — run CC delegation for each task in a feat.

Streams SSE events to the frontend as each task is executed:

    data: {"type": "task_start",  "task_id": "...", "task_number": N, "task_title": "..."}
    data: {"type": "chunk",       "text": "...", "task_id": "..."}
    data: {"type": "task_done",   "task_id": "...", "status": "done"|"failed"}
    data: {"type": "feat_done",   "feat_status": "...", "feat_id": "..."}
    data: {"type": "error",       "content": "..."}

Execution flow per task:
1. Mark task ``in_progress``
2. Build CC prompt (feat/task context + DESIGN.md)
3. Stream ``claude -p`` via :func:`~backend.services.claude_subprocess.run_claude_stream`
4. Mark task ``done`` or ``failed`` based on CC exit
5. Cascade status to feat → epic via :func:`~backend.services.task.recompute_feat_status`

A ``Delegation`` record is created at the start (status ``running``) and
finalized (``done`` / ``failed``) after all tasks complete.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.delegations import Delegation
from backend.db.models.projects import Project
from backend.db.models.specifications import DesignDocument
from backend.db.models.tasks import Epic, Feat, Task
from backend.services import claude_subprocess
from backend.services.task import recompute_feat_status

logger = logging.getLogger(__name__)

# Max DESIGN.md characters sent as context — keep prompts manageable.
_DESIGN_MAX_CHARS = 12_000


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _load_design_md(db: Session, project_id: UUID) -> str | None:
    """Load the latest approved DESIGN.md for the project."""
    stmt = (
        select(DesignDocument)
        .where(
            DesignDocument.project_id == project_id,
            DesignDocument.doc_type == "design",
            DesignDocument.approved_by.is_not(None),
        )
        .order_by(DesignDocument.version.desc())
        .limit(1)
    )
    doc = db.execute(stmt).scalar_one_or_none()
    if doc is None:
        # Fall back to any design doc (even unapproved).
        stmt2 = (
            select(DesignDocument)
            .where(
                DesignDocument.project_id == project_id,
                DesignDocument.doc_type == "design",
            )
            .order_by(DesignDocument.version.desc())
            .limit(1)
        )
        doc = db.execute(stmt2).scalar_one_or_none()
    return doc.content if doc else None


def _build_task_prompt(
    epic: Epic,
    feat: Feat,
    task: Task,
    design_content: str | None,
) -> str:
    """Build the CC prompt for a single task."""
    lines: list[str] = [
        "Implementuj nasledujúci task z NEX Studio Task Plan.",
        "",
        f"EPIC-{epic.number}: {epic.title}",
        f"Feat {epic.number}.{feat.number}: {feat.title}",
        f"Task {epic.number}.{feat.number}.{task.number}: {task.title}",
        f"Typ: {task.task_type}",
    ]

    if task.description:
        lines += ["", "Popis:", task.description]

    if design_content:
        lines += [
            "",
            "=== DESIGN.md (kontext projektu) ===",
            design_content[:_DESIGN_MAX_CHARS],
            "=== /DESIGN.md ===",
        ]

    lines += [
        "",
        "Implementuj task. Po implementácii spusti dostupné testy.",
        "Commitni zmeny s výstižnou commit správou v angličtine.",
    ]

    return "\n".join(lines)


async def execute_feat_stream(feat_id: UUID, db: Session) -> AsyncGenerator[str, None]:
    """Stream execution of all todo/failed tasks in the given feat.

    Args:
        feat_id: Primary key of the feat to execute.
        db: SQLAlchemy session (caller owns the lifecycle).

    Yields:
        SSE-formatted strings (``data: {...}\\n\\n``).
    """
    # ---- Load feat ---------------------------------------------------
    feat = db.get(Feat, feat_id)
    if feat is None:
        yield _sse({"type": "error", "content": f"Feat {feat_id} not found"})
        return

    epic = db.get(Epic, feat.epic_id)
    if epic is None:
        yield _sse({"type": "error", "content": "Epic not found"})
        return

    project = db.get(Project, epic.project_id)
    if project is None:
        yield _sse({"type": "error", "content": "Project not found"})
        return

    if not project.source_path:
        yield _sse({
            "type": "error",
            "content": (
                f"Project '{project.name}' nemá nastavený source_path. "
                "Nastav ho v Project Admin → Source Path."
            ),
        })
        return

    # ---- Load tasks (todo + failed, ordered) -------------------------
    tasks = list(
        db.execute(
            select(Task)
            .where(
                Task.feat_id == feat_id,
                Task.status.in_(["todo", "failed"]),
            )
            .order_by(Task.number.asc())
        )
        .scalars()
        .all()
    )

    if not tasks:
        yield _sse({"type": "error", "content": "Žiadne todo/failed tasky v tomto feate."})
        return

    # ---- Load DESIGN.md ----------------------------------------------
    design_content = _load_design_md(db, epic.project_id)

    # ---- Create Delegation record ------------------------------------
    delegation = Delegation(
        feat_id=feat_id,
        cc_agent="ubuntu_cc",
        prompt=f"Execute feat {epic.number}.{feat.number}: {feat.title} ({len(tasks)} tasks)",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(delegation)
    db.commit()
    db.refresh(delegation)

    # ---- Execute tasks sequentially ----------------------------------
    all_output: list[str] = []
    feat_failed = False

    for task in tasks:
        # Mark in_progress
        task.status = "in_progress"
        db.commit()

        yield _sse({
            "type": "task_start",
            "task_id": str(task.id),
            "task_number": task.number,
            "task_title": task.title,
        })

        prompt = _build_task_prompt(epic, feat, task, design_content)
        task_output: list[str] = []
        task_failed = False

        try:
            async for chunk in claude_subprocess.run_claude_stream(
                prompt=prompt,
                cwd=project.source_path,
            ):
                task_output.append(chunk)
                yield _sse({"type": "chunk", "text": chunk, "task_id": str(task.id)})
        except TimeoutError:
            task_failed = True
            yield _sse({"type": "chunk", "text": "\n[TIMEOUT — CC process exceeded time limit]\n", "task_id": str(task.id)})
        except RuntimeError as exc:
            task_failed = True
            yield _sse({"type": "chunk", "text": f"\n[ERROR: {exc}]\n", "task_id": str(task.id)})
        except Exception as exc:  # noqa: BLE001
            task_failed = True
            yield _sse({"type": "chunk", "text": f"\n[UNEXPECTED ERROR: {exc}]\n", "task_id": str(task.id)})
            logger.exception("Unexpected error executing task %s", task.id)

        # Update task status
        task.status = "failed" if task_failed else "done"
        if task_failed:
            feat_failed = True
        db.commit()

        # Cascade feat → epic status
        recompute_feat_status(db, feat_id)
        db.commit()

        all_output.append("".join(task_output))

        yield _sse({
            "type": "task_done",
            "task_id": str(task.id),
            "status": task.status,
        })

    # ---- Finalize Delegation record ----------------------------------
    delegation.status = "failed" if feat_failed else "done"
    delegation.raw_output = "\n---\n".join(all_output)
    delegation.completed_at = datetime.now(timezone.utc)
    db.commit()

    # ---- Reload feat status after cascade ----------------------------
    db.refresh(feat)

    yield _sse({
        "type": "feat_done",
        "feat_id": str(feat_id),
        "feat_status": feat.status,
    })

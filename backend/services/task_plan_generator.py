"""Task plan generator service — generates Epic→Feat→Task hierarchy from DESIGN.md.

Reads the project's DESIGN.md (and optionally BEHAVIOR.md) from the
design_documents table, calls Claude CLI via run_claude_stream, parses the AI
JSON response, and persists the resulting Epic/Feat/Task records under the
target version.

The generator is invoked from the versions router
(POST /versions/{version_id}/generate-task-plan) which streams SSE progress
events to the frontend as the generation proceeds.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncGenerator
from uuid import UUID

from sqlalchemy import func as sqlfunc
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.specifications import DesignDocument
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import claude_subprocess

logger = logging.getLogger(__name__)

# Valid task_type values — mirrors ck_tasks_task_type CHECK constraint on tasks table.
_VALID_TASK_TYPES = {"backend", "frontend", "migration", "test", "docs"}

_SYSTEM_PROMPT = (
    "Si ICC Architect AI. Tvojou úlohou je vygenerovať štruktúrovaný Task Plan"
    " pre implementáciu projektu na základe DESIGN.md dokumentu.\n\n"
    "VÝSTUP — povinný formát:\n"
    "Odpoveď musí byť VÝLUČNE validný JSON objekt — žiadny iný text pred ani po JSON.\n"
    "JSON schéma:\n"
    "{\n"
    '  "epics": [\n'
    "    {\n"
    '      "number": 1,\n'
    '      "title": "EPIC-1: Krátky popis (max 80 znakov)",\n'
    '      "feats": [\n'
    "        {\n"
    '          "number": 1,\n'
    '          "title": "Feat title (max 120 znakov)",\n'
    '          "description": "Podrobný popis čo sa implementuje",\n'
    '          "estimated_minutes": 60,\n'
    '          "tasks": [\n'
    "            {\n"
    '              "number": 1,\n'
    '              "title": "Task title (max 120 znakov)",\n'
    '              "description": "Konkrétne kroky, čo presne urobiť",\n'
    '              "task_type": "backend",\n'
    '              "checklist_type": "model",\n'
    '              "estimated_minutes": 30\n'
    "            }\n"
    "          ]\n"
    "        }\n"
    "      ]\n"
    "    }\n"
    "  ]\n"
    "}\n\n"
    "PRAVIDLÁ:\n"
    "- task_type: VÝLUČNE jedna z hodnôt: backend | frontend | migration | test | docs\n"
    "- checklist_type: model | schema | service | router | frontend | test | null\n"
    "- Každá entita z DESIGN.md musí mať tasks pre: migration → backend → frontend\n"
    "- EPIC-1 musí obsahovať foundation (auth + seed user) ak je to user-facing app\n"
    "- Poradie taskov v rámci feat: migration → backend → frontend → test\n"
    "- Číslovanie: epic.number globálne (1, 2, 3...), feat.number v rámci epic (1, 2...),"
    " task.number v rámci feat (1, 2...)\n"
    "- estimated_minutes: realistický odhad pre každý task (15-240 min)\n"
    "- Žiadne placeholder texty, žiadne TODO\n"
    "- MAXIMÁLNA GRANULARITA: radšej viac menších taskov ako jeden veľký\n"
    "- POVINNÉ: vráť ČISTÝ JSON bez ```json``` fences a bez akéhokoľvek textu pred/po JSON\n"
)


def _load_design_doc(db: Session, project_id: UUID, doc_type: str) -> str | None:
    """Return the latest approved design document content, or None.

    Prefers an approved version; falls back to the latest unapproved version
    so generation is not blocked when the doc was not formally approved yet.
    """
    stmt_approved = (
        select(DesignDocument)
        .where(
            DesignDocument.project_id == project_id,
            DesignDocument.doc_type == doc_type,
            DesignDocument.approved_by.is_not(None),
        )
        .order_by(DesignDocument.version.desc())
    )
    doc = db.execute(stmt_approved).scalars().first()
    if doc is None:
        stmt_latest = (
            select(DesignDocument)
            .where(
                DesignDocument.project_id == project_id,
                DesignDocument.doc_type == doc_type,
            )
            .order_by(DesignDocument.version.desc())
        )
        doc = db.execute(stmt_latest).scalars().first()
    return doc.content if doc else None


def _extract_json(text: str) -> dict | None:
    """Extract the first valid JSON object from a text response."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip optional ```json ... ``` fence
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass
    # Find first balanced { ... } block
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _normalise_task_type(value: str | None) -> str:
    """Map AI-generated task_type to a valid DB value; default 'backend'."""
    if not value:
        return "backend"
    v = value.lower().strip()
    if v in _VALID_TASK_TYPES:
        return v
    aliases: dict[str, str] = {
        "implementation": "backend",
        "api": "backend",
        "db": "migration",
        "database": "migration",
        "ui": "frontend",
        "react": "frontend",
        "tests": "test",
        "documentation": "docs",
        "infra": "backend",
    }
    return aliases.get(v, "backend")


def _normalise_checklist_type(value: str | None) -> str | None:
    """Return checklist_type if valid, else None."""
    valid = {"model", "schema", "service", "router", "frontend", "test"}
    if value and str(value).lower() in valid:
        return str(value).lower()
    return None


async def generate_task_plan_stream(
    version_id: UUID,
    project_id: UUID,
    db: Session,
    replace_existing: bool = False,
) -> AsyncGenerator[str, None]:
    """Stream task plan generation as SSE-formatted strings.

    Yields SSE event strings::

        data: {"type": "progress", "message": "...", "percent": N}
        data: {"type": "done", "plan": [...], "epic_count": N, "feat_count": N, "task_count": N}
        data: {"type": "error", "content": "..."}
        data: {"type": "validation_error", "content": "..."}

    Args:
        version_id: The release version under which epics are created.
        project_id: The parent project (used for design doc lookup + epic number scoping).
        db: A dedicated SQLAlchemy session — caller owns commit/rollback.
        replace_existing: When True, delete all existing epics under this version
            before inserting the new plan.
    """

    def _sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    yield _sse({"type": "progress", "message": "Načítavam DESIGN.md…", "percent": 5})

    # Load DESIGN.md — required.
    design_content = _load_design_doc(db, project_id, "design")
    if not design_content:
        yield _sse({
            "type": "validation_error",
            "content": "Projekt nemá DESIGN.md. Najprv vygeneruj DESIGN.md cez Specification pipeline.",
        })
        return

    # Load BEHAVIOR.md — optional supplementary context.
    behavior_content = _load_design_doc(db, project_id, "behavior")

    yield _sse({"type": "progress", "message": "Pripravujem prompt pre Claude…", "percent": 10})

    user_prompt_parts: list[str] = [
        "Na základe nasledovného DESIGN.md dokumentu vygeneruj kompletný Task Plan"
        " pre implementáciu projektu. Vráť VÝLUČNE JSON podľa schémy zo system promptu.\n\n"
        "## DESIGN.md\n\n",
        design_content[:40000],  # Cap to avoid exceeding context limits
    ]
    if behavior_content:
        user_prompt_parts.extend([
            "\n\n## BEHAVIOR.md (doplnkový kontext pre workflows a edge cases)\n\n",
            behavior_content[:15000],
        ])
    user_prompt_parts.append(
        "\n\nVygeneruj Task Plan. Vráť VÝLUČNE čistý JSON — žiadny iný text."
    )
    user_prompt = "".join(user_prompt_parts)

    yield _sse({"type": "progress", "message": "Volám Claude AI…", "percent": 15})

    # Collect full AI response via streaming.
    full_response_parts: list[str] = []
    chunk_count = 0
    try:
        async for chunk in claude_subprocess.run_claude_stream(
            prompt=user_prompt,
            context=_SYSTEM_PROMPT,
            timeout=900,  # 15 min — task plans can be large
        ):
            full_response_parts.append(chunk)
            chunk_count += 1
            if chunk_count % 25 == 0:
                percent = min(15 + (chunk_count // 25), 72)
                yield _sse({"type": "progress", "message": "AI generuje task plan…", "percent": percent})
    except (RuntimeError, TimeoutError) as exc:
        logger.error("Claude error during task plan generation for version %s: %s", version_id, exc)
        yield _sse({"type": "error", "content": str(exc)})
        return

    full_response = "".join(full_response_parts)
    yield _sse({"type": "progress", "message": "Parsovanie JSON odpovede…", "percent": 75})

    plan_data = _extract_json(full_response)
    if plan_data is None or "epics" not in plan_data:
        logger.error(
            "Failed to parse task plan JSON for version %s. Response snippet: %.600s",
            version_id,
            full_response,
        )
        yield _sse({
            "type": "validation_error",
            "content": "Claude nevrátil validný JSON s kľúčom 'epics'. Skús generovanie znovu.",
        })
        return

    epics_data = plan_data["epics"]
    if not isinstance(epics_data, list) or not epics_data:
        yield _sse({"type": "validation_error", "content": "Task Plan neobsahuje žiadne EPICy."})
        return

    yield _sse({"type": "progress", "message": "Ukladám do databázy…", "percent": 78})

    # Optionally delete existing plan under this version.
    if replace_existing:
        existing_epics = db.execute(
            select(Epic).where(Epic.version_id == version_id)
        ).scalars().all()
        for ep in existing_epics:
            db.delete(ep)
        db.flush()

    # Verify version exists.
    version = db.get(Version, version_id)
    if version is None:
        yield _sse({"type": "error", "content": f"Version {version_id} not found"})
        return

    # Compute epic number offset so we don't collide with existing epics in the project.
    max_num = db.execute(
        select(sqlfunc.max(Epic.number)).where(Epic.project_id == project_id)
    ).scalar() or 0
    epic_number_offset = max_num if not replace_existing else 0

    total_epics = len(epics_data)
    created_epics = 0
    created_feats = 0
    created_tasks = 0
    plan_summary: list[dict] = []

    for epic_data in epics_data:
        ai_epic_num = epic_data.get("number") or (created_epics + 1)
        epic_num = epic_number_offset + ai_epic_num
        epic_title = str(epic_data.get("title") or f"EPIC-{epic_num}")[:500]

        epic_orm = Epic(
            project_id=project_id,
            version_id=version_id,
            number=epic_num,
            title=epic_title,
            status="planned",
        )
        db.add(epic_orm)
        db.flush()
        created_epics += 1

        epic_summary: dict = {
            "id": str(epic_orm.id),
            "number": epic_orm.number,
            "title": epic_orm.title,
            "status": epic_orm.status,
            "feats": [],
        }

        for feat_data in epic_data.get("feats", []):
            feat_num = feat_data.get("number") or (created_feats + 1)
            feat_title = str(feat_data.get("title") or f"Feat {feat_num}")[:500]
            feat_desc = str(feat_data.get("description") or "")
            feat_est_raw = feat_data.get("estimated_minutes")
            feat_est = int(feat_est_raw) if isinstance(feat_est_raw, (int, float)) else None

            feat_orm = Feat(
                epic_id=epic_orm.id,
                number=feat_num,
                title=feat_title,
                description=feat_desc,
                status="todo",
                estimated_minutes=feat_est,
            )
            db.add(feat_orm)
            db.flush()
            created_feats += 1

            feat_summary: dict = {
                "id": str(feat_orm.id),
                "number": feat_orm.number,
                "title": feat_orm.title,
                "status": feat_orm.status,
                "tasks": [],
            }

            task_num_in_feat = 0
            for task_data in feat_data.get("tasks", []):
                task_num_in_feat += 1
                task_num = task_data.get("number") or task_num_in_feat
                task_title = str(task_data.get("title") or f"Task {task_num}")[:500]
                task_desc = str(task_data.get("description") or "")
                task_type = _normalise_task_type(task_data.get("task_type"))
                checklist_type = _normalise_checklist_type(task_data.get("checklist_type"))
                task_est_raw = task_data.get("estimated_minutes")
                task_est = int(task_est_raw) if isinstance(task_est_raw, (int, float)) else None

                task_orm = Task(
                    feat_id=feat_orm.id,
                    number=task_num,
                    title=task_title,
                    description=task_desc,
                    task_type=task_type,
                    status="todo",
                    estimated_minutes=task_est,
                    checklist_type=checklist_type,
                )
                db.add(task_orm)
                created_tasks += 1

                feat_summary["tasks"].append({
                    "number": task_num,
                    "title": task_title,
                    "task_type": task_type,
                    "status": "todo",
                })

            db.flush()
            epic_summary["feats"].append(feat_summary)

        plan_summary.append(epic_summary)
        percent = 78 + int(17 * created_epics / total_epics)
        yield _sse({
            "type": "progress",
            "message": f"EPIC-{epic_num} vytvorený ({created_epics}/{total_epics})…",
            "percent": percent,
        })

    db.commit()

    logger.info(
        "Task plan generated for version %s: %d epics, %d feats, %d tasks",
        version_id,
        created_epics,
        created_feats,
        created_tasks,
    )

    yield _sse({
        "type": "done",
        "plan": plan_summary,
        "epic_count": created_epics,
        "feat_count": created_feats,
        "task_count": created_tasks,
    })

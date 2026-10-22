"""Puts the agent's customer sentence back into the fast fixes built while the cockpit dropped it (DEV-97).

From 07.09.2026 the fast-fix epic carried the manager's directive and the agent's sentence was written only into a
blank field — so it was never written, and the generated release note listed the agent brief. Career Asistent 0.1.1
showed eight English instruction lines as what to try in UAT; NEX Inbox 1.5.2 told its customer to execute a spec
file.

The decisions are the cockpit's own: the sentence goes in by :func:`orchestrator._apply_customer_note_to_fix_epic`,
the note is rendered and committed by the same path a finished build uses. A released version stays as it was,
a note a person or the agent wrote is left alone, and a project whose build an agent is working on right now is
skipped — the note commit would land under its feet. Without ``--apply`` it prints what would change and writes
nothing. Runs in the cockpit backend container::

    docker exec nex-studio-visual-prod-backend-1 python -m backend.scripts.repair_fast_fix_notes
    docker exec nex-studio-visual-prod-backend-1 \
        python -m backend.scripts.repair_fast_fix_notes --apply --project career-asistent
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.db.session import SessionLocal
from backend.services import claude_agent, orchestrator, release_note_writer


def repair(db: Session, *, projects_root: Path, only: set[str], apply: bool) -> list[str]:
    """What the repair changes, one line per version and step; with ``apply`` it also writes it."""
    rows = db.execute(
        select(Version, Project.slug)
        .join(PipelineState, PipelineState.version_id == Version.id)
        .join(Project, Project.id == Version.project_id)
        .where(PipelineState.flow_type == "fast_fix")
        .order_by(Project.slug, Version.created_at)
    ).all()
    busy = set(
        db.execute(
            select(Project.slug)
            .join(Version, Version.project_id == Project.id)
            .join(PipelineState, PipelineState.version_id == Version.id)
            .where(PipelineState.status == "agent_working")
        ).scalars()
    )
    lines: list[str] = []
    savepoint = db.begin_nested()
    for version, slug in rows:
        if only and slug not in only:
            continue
        label = f"{slug} {version.version_number}"
        if orchestrator._apply_customer_note_to_fix_epic(db, version.id):
            lines.append(f"{label}: veta agenta pre zákazníka nahradí v epike zadanie")
        root = projects_root / slug
        note = release_note_writer.version_notes_dir(root, version.version_number) / "RELEASE_NOTES.md"
        if version.status == "released" or not note.is_file():
            continue
        rendered = release_note_writer.render_release_note(db, version)
        existing = note.read_text(encoding="utf-8")
        if existing.strip() == rendered.strip():
            continue
        if not release_note_writer._is_ours(existing, rendered):
            lines.append(f"{label}: poznámky k vydaniu napísal človek alebo agent — nechávam ich tak")
            continue
        if slug in busy:
            lines.append(f"{label}: na projekte práve pracuje agent — poznámky prepíšem pri ďalšom spustení")
            continue
        lines.append(f"{label}: poznámky k vydaniu prepíšem vetou agenta")
        if apply:
            asyncio.run(orchestrator._commit_release_note(db, version.id, root, version.version_number))
    if apply:
        savepoint.commit()
    else:
        savepoint.rollback()
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Veta agenta pre zákazníka späť do poznámok rýchlych opráv.")
    parser.add_argument("--apply", action="store_true", help="zapísať (bez neho len výkaz)")
    parser.add_argument("--project", action="append", default=[], help="len tento projekt (dá sa zopakovať)")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        lines = repair(db, projects_root=claude_agent.PROJECTS_ROOT, only=set(args.project), apply=args.apply)
        for line in lines:
            print(line)
        if not lines:
            print("nič na opravu")
        if not args.apply:
            print("NANEČISTO — nič sa nezapísalo. Zápis: --apply")
            return 0
        db.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

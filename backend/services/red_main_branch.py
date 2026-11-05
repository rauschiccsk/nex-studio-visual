"""DEV-260 — the next task of a build does not start on a main branch the version's own tasks left red.

03.11.2026, NEX Manager 1.5.0: task #35.2.1 committed a test file that failed the backend's format check; the main
branch stayed red through task #35.2.2 until task #36.1.1 fixed it, sent back by the CI gate at Verifikácia (DEV-261).
The rule „every task leaves the main branch green“ was a sentence in the task's brief, and nothing made it hold. The
cockpit pushes the branch after every task (DEV-96) and reads GitHub's runs (``ci_runs.listing``, the source the
repository tab and ``ci_watch`` share), so it knows: when the newest run of a workflow on the main branch finished red
on a commit of this version's tasks, the agent's next turn fixes the branch first and then goes on with the plan.

A run of another version's commit, a planned red proof on a side branch, or a red run with a newer one still running
(the fix on its way) does not stop anything — the gate at Verifikácia stays for what this one does not see.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import ci_runs, ci_watch


def red_runs(runs: list[ci_runs.Run], branch: str) -> list[ci_runs.Run]:
    """Per workflow, the newest run on *branch* — kept when it finished red. *runs* come newest first (GitHub's
    order), so a fix still running masks the red run under it."""
    newest: dict[str, ci_runs.Run] = {}
    for run in runs:
        if run.branch == branch:
            newest.setdefault(run.workflow, run)
    return [run for run in newest.values() if run.state == ci_runs.FAILURE]


def fix_first_brief(reds: list[tuple[ci_runs.Run, ci_runs.TaskLink]]) -> str:
    """What the agent reads before its task: which check of which task failed, and that the fix comes first."""
    lines = [
        f"- úloha #{link.label} ({link.title}): kontrola „{run.workflow}“"
        + (f" zlyhala v časti „{run.failed_step}“" if run.failed_step else " zlyhala")
        + (f" — {run.url}" if run.url else "")
        for run, link in reds
    ]
    return (
        "Hlavná vetva projektu je po úlohách tejto verzie červená:\n"
        + "\n".join(lines)
        + "\n\nSkôr než začneš úlohu plánu nižšie, oprav hlavnú vetvu: spusti kontrolu, ktorá zlyhala (a rýchle "
        "kontroly projektu, ktoré sa tu dajú spustiť — formát, lint, skúšky), oprav príčinu a opravu commitni "
        "samostatne. Potom pokračuj úlohou plánu nižšie — správu na konci ťahu odovzdávaš za ňu."
    )


async def reds_of_version(
    db: Session, version_id: uuid.UUID, project_root: Path, *, timeout: float
) -> list[tuple[ci_runs.Run, ci_runs.TaskLink]]:
    """The red runs on the project's main branch whose commit is one of this version's tasks, each with its task.
    Empty when GitHub, the branch or the repository cannot be read — the gate at Verifikácia still stands."""
    version = db.get(Version, version_id)
    project = db.get(Project, version.project_id) if version else None
    repo = ci_runs.repo_of(project.repo_url) if project else None
    if not repo:
        return []
    branch = await asyncio.to_thread(ci_watch.checkout_branch, project_root)
    if not branch:
        return []
    listing = await ci_runs.listing(repo, timeout=timeout)
    if listing.error:
        return []  # an old answer may show a red the agent already fixed — no stop on it
    reds = red_runs(listing.runs, branch)
    labels = {label for run in reds if (label := ci_runs.task_label(run.subject))}
    links = ci_runs.task_links(db, project.id, labels)
    return [
        (run, links[label])
        for run in reds
        if (label := ci_runs.task_label(run.subject)) in links and links[label].version_id == version_id
    ]

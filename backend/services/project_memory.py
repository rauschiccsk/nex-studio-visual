"""AI-Agent per-project persistent memory (CR-V2-016, OQ-4 = file-based).

The v2.0.0 AI Agent has its **own persistent per-project memory** that v1
NEX Studio lacked — it reads the memory at session start and **writes freely**
(decisions, lessons, context, Manažér feedback), recalling it on future builds
of the same project. This is the "exactly like Dedo" model
(``docs/architecture/nex-studio-v2-design.md`` §5.2; build-plan CR-V2-016,
OQ-4 RESOLVED 2026-06-26 = per-project ``MEMORY.md`` — no schema, no migration,
human-readable, git-diffable, travels with the project).

Single-writer by construction (R-DOUBLEWRITE)
=============================================
The memory lives **in the project workspace** — ``/opt/projects/<slug>/MEMORY.md``
(+ optional topic files under ``/opt/projects/<slug>/.memory/``) — which is the
AI Agent's ``cwd`` when the engine drives it (``claude_agent._invoke_once`` runs
with ``cwd=/opt/projects/<slug>/``). The agent therefore reads and writes the
memory **with its own ``Read``/``Write`` tools**; there is deliberately **no
backend auto-writer of ``MEMORY.md``**. That is the whole point of the
single-source-of-truth resolution: the agent is the *only* writer of the
status/history/decision content, so the file can never drift from a second,
DB-driven generator.

This module is therefore intentionally **write-free for ``MEMORY.md``**. It
provides only:

* :data:`MEMORY_FILENAME` — the path convention; :func:`memory_path` resolves it for a project workspace.
* :func:`seed_memory` — write the *initial* ``MEMORY.md`` skeleton **once** at
  project creation (a one-shot scaffold, not an ongoing writer — it no-ops if a
  memory already exists, so it never overwrites the agent's free writes).

The agent reads its memory itself (the file is in its ``cwd``). DEV-155 removed a backend read, a topic-dir resolver
and a shared-KB reindex hook that nothing called.

The retired live-document writers (``STATUS.md`` / ``HISTORY.md`` DB-driven
persistence) are gone, along with the live-document generators that read them. The status /
history narrative now lives in this memory (agent-written) plus the Vývoj phase
tabs (design §5.3).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from backend.services.claude_agent import PROJECTS_ROOT

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

#: The AI Agent's primary per-project memory file, at the workspace root so it is
#: the agent's ``cwd``-relative ``MEMORY.md`` (mirrors the Dedo ``MEMORY.md``).
MEMORY_FILENAME = "MEMORY.md"


def workspace_root(project_slug: str) -> Path:
    """Return the absolute project workspace root (``/opt/projects/<slug>/``).

    This is the AI Agent's ``cwd`` for every engine-driven turn, so a path
    resolved under it is exactly what the agent's ``Read``/``Write`` tools see.
    """
    return PROJECTS_ROOT / project_slug


def memory_path(project_slug: str) -> Path:
    """Return the absolute path to the project's ``MEMORY.md``."""
    return workspace_root(project_slug) / MEMORY_FILENAME


def _seed_skeleton(project_name: str) -> str:
    """Return the initial ``MEMORY.md`` content for a freshly created project.

    Deliberately minimal — a header + the section scaffold the charter rules
    point the agent at. The agent fills it in across builds; this is only the
    starting page so a first build reads a well-formed file, not a 404.
    """
    return (
        f"# {project_name} — AI Agent pamäť\n"
        "\n"
        "> Perzistentná per-project pamäť AI Agenta (CR-V2-016). Čítam ju na začiatku\n"
        "> každého buildu a **píšem do nej voľne** — rozhodnutia, lekcie, kontext a\n"
        "> feedback Manažéra. Jediný zdroj pravdy pre status/históriu projektu\n"
        "> (STATUS.md/HISTORY.md sú retired — viď Vývoj fázové taby).\n"
        "\n"
        "## Rozhodnutia\n"
        "\n"
        "## Lekcie\n"
        "\n"
        "## Kontext\n"
        "\n"
        "## Feedback Manažéra\n"
    )


def seed_memory(project_slug: str, project_name: str) -> Path | None:
    """Write the initial ``MEMORY.md`` skeleton for a new project, **once**.

    A one-shot scaffold at project creation — **not** an ongoing writer. It
    no-ops (returns ``None``) if a memory file already exists, so it can never
    overwrite the agent's own free writes on a re-create / idempotent replay.
    Returns the path written, or ``None`` when an existing memory was preserved
    or the workspace does not exist yet (library / test projects with no
    checkout).

    The single-writer invariant (R-DOUBLEWRITE) is preserved: this seeds the
    file the agent then owns; after seeding, the **agent is the only writer**.
    """
    root = workspace_root(project_slug)
    if not root.is_dir():
        # No checkout on disk (e.g. a library/test project) — nothing to seed.
        return None
    target = memory_path(project_slug)
    if target.exists():
        # Never clobber the agent's accumulated memory.
        return None
    target.write_text(_seed_skeleton(project_name), encoding="utf-8")
    logger.info("Seeded AI-Agent memory for project=%s at %s", project_slug, target)
    return target

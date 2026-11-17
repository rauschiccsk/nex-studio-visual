"""What a deploy reads the project from — an export of the commit the deployed version's check is bound to (DEV-420).

**Why.** Until 17.11.2026 a deploy read the project's working folder: its images were built from ``HEAD``, its compose,
``.env.example`` and settings contract were read as they stood there. A version could therefore be deployed only while
``HEAD`` was its verified commit — NEX Manager 1.6.0 could not go to a customer while 1.7.0 was being built, and the
released 1.2.2 was offered with the code of 1.7.0 under its number (DEV-416). Director: „Súhlasím“.

**How.** :func:`export` writes the tracked files of that commit (``git archive``) into a throwaway folder named like the
project, so everything that reads a project folder reads it unchanged; :func:`remove` drops it after the deploy. The
images are built by verified_build from the same commit.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from backend.services import verified_build

#: Why a version has no commit to deploy from (``orchestrator.deployable_commit``): no current check, no commit on
#: record, or a recorded commit the repository no longer has.
NO_PASS = "no_pass"
NO_COMMIT = "no_commit"
COMMIT_MISSING = "commit_missing"


@dataclass(frozen=True)
class DeploySource:
    """The project as the deployed version has it: ``path`` holds the tracked files of ``commit``."""

    commit: str
    path: Path


def export(repo: Path, commit: str) -> DeploySource:
    """Export ``commit`` of the repository at ``repo`` into a new folder named like the project. Blocking (git, tar) —
    call it off the event loop. Raises ``RuntimeError`` / ``OSError`` when it cannot."""
    root = Path(tempfile.mkdtemp(prefix="nsv-deploy-source-"))
    try:
        verified_build.export_commit(repo, commit, root / repo.name)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return DeploySource(commit, root / repo.name)


def remove(source: DeploySource) -> None:
    """Drop the export — after the deploy, whatever its outcome."""
    shutil.rmtree(source.path.parent, ignore_errors=True)

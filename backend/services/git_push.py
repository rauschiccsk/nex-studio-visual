"""DEV-161 — the one way the cockpit sends a project's commits to GitHub.

The backend container's git signs in to GitHub through gh's credential helper. ``gh auth setup-git`` writes it into the
container's global gitconfig, and every recreate of the container — every cockpit deploy — throws it away. Each push
used to wire it on its own or not at all: the founding and the build did, the switch to Web did not, and Career
Asistent's switch stayed on ANDROS (21.10.2026: „could not read Username for 'https://github.com'“; Director: „Áno treba
opraviť a nasaď.“). :func:`push` wires the helper before every push, and
``tests/test_every_push_signs_in_first.py`` refuses a git push anywhere else in the backend.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def wire_credentials(*, timeout: int) -> None:
    """``gh auth setup-git`` — idempotent. A failure is logged, never raised: the push itself then says why it could
    not sign in."""
    try:
        done = subprocess.run(["gh", "auth", "setup-git"], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("gh auth setup-git could not run (the push will say why it cannot sign in): %s", exc)
        return
    if done.returncode != 0:
        logger.warning(
            "gh auth setup-git returned %d (the push will say why it cannot sign in): %s",
            done.returncode,
            done.stderr.strip()[:300],
        )


def push(root: Path, *args: str, timeout: int) -> subprocess.CompletedProcess:
    """``git push <args>`` in the repository *root*, after :func:`wire_credentials`.

    Never raises: a git that cannot run or does not finish within *timeout* comes back as exit 1 with the reason in
    ``stderr``."""
    wire_credentials(timeout=timeout)
    argv = ["git", "-C", str(root), "push", *args]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 1, "", str(exc))

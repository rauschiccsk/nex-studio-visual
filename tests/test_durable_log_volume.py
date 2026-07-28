"""The durable agent-log volume mount MUST land on the path the code writes to.

The audited bug: ``docker-compose.yml`` mounted the ``terminal_logs`` volume at
``/var/lib/nex-studio-visual/terminal-logs`` while both writers used ``/var/lib/nex-studio/terminal-logs``.
Nothing failed — the writes landed in the container's writable layer — so the volume stayed empty and
every agent crash/timeout was undiagnosable after the next recreate. A path mismatch of this shape is
invisible to any runtime test, so it is pinned HERE, against the compose file itself.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from backend.constants.paths import TERMINAL_LOG_DIR
from backend.services import agent_terminal, claude_agent

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "docker-compose.yml"

#: The named volume both log writers depend on.
_VOLUME_NAME = "terminal_logs"


def _backend_mount_targets() -> dict[str, str]:
    """``{volume-or-source: target}`` for every ``short syntax`` bind/volume of the backend service."""
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    targets: dict[str, str] = {}
    for entry in compose["services"]["backend"]["volumes"]:
        source, target = entry.split(":")[0], entry.split(":")[1]
        targets[source] = target
    return targets


def test_compose_mounts_the_volume_where_the_code_writes() -> None:
    targets = _backend_mount_targets()
    assert _VOLUME_NAME in targets, f"backend service no longer mounts the {_VOLUME_NAME!r} volume"
    assert targets[_VOLUME_NAME] == str(TERMINAL_LOG_DIR), (
        f"docker-compose.yml mounts {_VOLUME_NAME} at {targets[_VOLUME_NAME]!r} but the backend writes to "
        f"{str(TERMINAL_LOG_DIR)!r} — the volume would stay empty and every crash log would die on recreate"
    )


def test_the_volume_is_declared() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert _VOLUME_NAME in (compose.get("volumes") or {}), "named volume declaration disappeared"


def test_both_writers_share_the_one_constant() -> None:
    """Two independent literals for one volume is how the paths drifted apart — keep them derived.

    ``TURN_LOG_DIR`` stays env-overridable (``NEX_TURN_LOG_DIR``, for a bare-metal run); the assertion is
    on its DEFAULT, which is what the container actually gets.
    """
    assert agent_terminal.TERMINAL_LOG_DIR == TERMINAL_LOG_DIR
    assert claude_agent.TURN_LOG_DIR == Path(os.environ.get("NEX_TURN_LOG_DIR", str(TERMINAL_LOG_DIR)))

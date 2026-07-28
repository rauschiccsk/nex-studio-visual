"""Filesystem locations that MUST match the container's declared Docker volume mounts.

A code path that no volume is mounted at looks perfectly healthy: the write succeeds (into the
container's writable layer) and the data vanishes on the next ``docker compose up`` recreate. That is
exactly how the durable agent logs were lost — the writers used ``/var/lib/nex-studio/terminal-logs``
while BOTH compose files mount the ``terminal_logs`` volume at
``/var/lib/nex-studio-visual/terminal-logs``, so the volume stayed empty and every crash/timeout was
undiagnosable (the very failure :data:`backend.services.claude_agent.TURN_LOG_DIR` was introduced to
prevent).

The mismatch was resolved TOWARDS COMPOSE (code moved to ``…/nex-studio-visual/…``) because:

* the deployed PROD instance already mounts the ``-visual`` path (``/opt/customers/dev/
  nex-studio-visual/docker-compose.yml``), an artifact outside this repo — changing the compose side
  would have fixed only the dev view and left the running deployment writing into a doomed layer;
* every other per-instance path this project owns already carries the project's own name
  (``/opt/data/nex-studio-visual/credentials``, the ``nex-studio-visual-*`` image tags);
  ``/var/lib/nex-studio`` was inherited verbatim from the parent nex-studio checkout and would collide
  with it on a host bind mount.

Both writers now derive from the ONE constant below, and ``tests/test_durable_log_volume.py`` asserts it
against the mount target parsed out of ``docker-compose.yml`` — so the next divergence fails a test
instead of silently emptying the volume.
"""

from __future__ import annotations

from pathlib import Path

#: Root of the durable, volume-backed state this backend writes. Mounted in ``docker-compose.yml``
#: (backend service, named volume ``terminal_logs``); survives container recreate.
DURABLE_STATE_ROOT = Path("/var/lib/nex-studio-visual")

#: The durable log directory shared by the PTY scrollback (:mod:`backend.services.agent_terminal`) and
#: the per-turn crash diagnostics (:mod:`backend.services.claude_agent`). ONE constant on purpose: two
#: independent literals for one volume is how the paths drifted apart in the first place.
TERMINAL_LOG_DIR = DURABLE_STATE_ROOT / "terminal-logs"

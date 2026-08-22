"""The environment a spawned agent gets: this process's, MINUS the credentials it must never carry.

THE PROBLEM THIS EXISTS FOR. The backend spawns the AI Agent — ``claude`` with an unrestricted Bash tool
(build turns run with ``allowed_tools=None``) — and a child inherits its parent's whole environment unless
somebody says otherwise. Anything in ``os.environ`` is therefore something the agent can print. That is
merely untidy for most variables and a hole in the design for ONE of them: Dedo's machine token
(ICCINT-14) is the credential that can clear a ``framework_issue`` block, and the party that RAISES those
blocks is the agent. Handed the token, an agent whose build is stuck can write into the thread as ``dedo``
and release itself — the exact impersonation charter §4.5 forbids, and the exact opposite of the charter's
*"Hranica má byť vynútená TOKENOM, nie disciplínou volajúceho."*

WHY THIS IS THE SECOND LINE AND NOT THE FIRST. :func:`backend.config.settings._take_dedo_plaintext_token`
pops ``DEDO_API_TOKEN`` out of ``os.environ`` at import, which covers every child including the many that
inherit implicitly. This function covers what that cannot: a variable re-introduced at runtime, and a spawn
site that builds its env by hand. Both lines together still do not beat a root shell reading
``/proc/1/environ``, which is why the RECOMMENDED configuration keeps only a digest in this container
(:data:`backend.config.settings.Settings.dedo_api_token_sha256`) and no secret at all.

WHAT IS NOT STRIPPED, AND WHY. ``GITHUB_TOKEN`` / ``GH_TOKEN`` stay: the agent pushes branches and opens
PRs with them, so they are working tools, not a boundary being handed to the party it binds. Removing them
would break builds and is a separate decision, not a side effect of this fix.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: Environment variables NEVER handed to a spawned agent.
WITHHELD_FROM_AGENTS: tuple[str, ...] = ("DEDO_API_TOKEN",)


def agent_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """``os.environ`` minus :data:`WITHHELD_FROM_AGENTS`, plus ``extra``.

    Use this at EVERY site that spawns a process the AI Agent controls or can write the script for —
    the headless turn, the agent terminal, the project's own ``release_smoke_test.sh``.
    """
    env = {key: value for key, value in os.environ.items() if key not in WITHHELD_FROM_AGENTS}
    if extra:
        env.update(extra)
    return env

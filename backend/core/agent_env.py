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

GITHUB (DEV-179). ``GITHUB_TOKEN`` / ``GH_TOKEN`` were the cockpit's own token — it reaches every repository of
the account and administers the cockpit's — and the ban on ``git push`` in the agent's permissions caught one shape
of the command only (9.–13.10.2026 the NEX Inbox agent pushed 38 times with ``git -c credential.helper=… push``).
Since DEV-173 the cockpit sends everything to GitHub itself, so under those two names the agent gets
:data:`AGENT_GITHUB_TOKEN` — a token the Director created to READ (CI results, the repositories' contents) — or no
GitHub token at all. Director 21.10.2026: „Súhlasím“.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: Environment variables NEVER handed to a spawned agent.
#: DEV-117: ``PLANE_API_TOKEN`` is the cockpit's own identity in Plane. Tickets are founded by a person's click; an
#: agent holding the key could write into the ticket register as the cockpit — the impersonation Dedo's token guards.
WITHHELD_FROM_AGENTS: tuple[str, ...] = ("DEDO_API_TOKEN", "PLANE_API_TOKEN")

#: DEV-179 — the names under which git and gh look for a GitHub token; an agent finds the reading token there.
GITHUB_TOKEN_NAMES: tuple[str, ...] = ("GITHUB_TOKEN", "GH_TOKEN")
#: The token an agent may read GitHub with; never handed over under its own name.
AGENT_GITHUB_TOKEN = "AGENT_GITHUB_TOKEN"


def agent_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """``os.environ`` minus :data:`WITHHELD_FROM_AGENTS` and the cockpit's GitHub token, plus ``extra`` — with the
    reading token :data:`AGENT_GITHUB_TOKEN` under :data:`GITHUB_TOKEN_NAMES` when it is set (DEV-179).

    Use this at EVERY site that spawns a process the AI Agent controls or can write the script for —
    the headless turn, the agent terminal, the project's own ``release_smoke_test.sh``.
    """
    held_back = (*WITHHELD_FROM_AGENTS, *GITHUB_TOKEN_NAMES, AGENT_GITHUB_TOKEN)
    env = {key: value for key, value in os.environ.items() if key not in held_back}
    if extra:
        env.update({key: value for key, value in extra.items() if key not in held_back})
    reading = os.environ.get(AGENT_GITHUB_TOKEN, "").strip()
    if reading:
        env.update(dict.fromkeys(GITHUB_TOKEN_NAMES, reading))
    return env

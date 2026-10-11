"""Poradca — an agent that people in the cockpit consult; it only reads (ICCINT-167, ``docs/specs/poradca.md``).

Moduly:
  * :mod:`.secrets_filter` — secrets are replaced with „‹skryté›" before Poradca gets them, and once more
    before the answer is saved;
  * :mod:`.sandbox` — a temporary container of one question: Claude Code restricted mode, project read-only,
    files with secrets overlaid with empty ones;
  * :mod:`.mcp_server` + :mod:`.shim` — the tools "from behind" are served by the backend over one question's socket;
  * :mod:`.tools` — the tools themselves;
  * :mod:`.runner` — running a question, progress, stopping, the concurrency limit.
"""

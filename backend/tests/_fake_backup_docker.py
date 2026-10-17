"""What a faked Docker answers to the commands of DEV-116's backup before a PROD deploy.

Shared by the deploy tests that fake ``asyncio.create_subprocess_exec``: once a PROD deploy backs the database up
between the build and ``up``, every such fake meets these commands — and a rule like „every Docker command of a
deploy elsewhere goes to that machine" must see them too, not have them stubbed away.
"""

from __future__ import annotations

from typing import Optional

from backend.services import predeploy_backup as B

DB_BYTES = 5 * 1024**2
FREE_KB = 50 * 1024**2
DUMP_BYTES = 123_456
SHA = "ab" * 32
#: The image the installation's database container runs from (``docker compose images -q <service>``).
DB_IMAGE = "sha256:0db1ma6e"
#: ``pg_restore -l`` of a small dump: comment lines start with ``;``, every other line is one entry.
TOC = (
    ";\n; Archive created at 2026-10-14 12:50:00 UTC\n;\n"
    "215; 1259 16385 TABLE public invoices nex\n216; 1259 16390 TABLE public emails nex\n"
)


def df_line(free_kb: int) -> str:
    header = "Filesystem 1024-blocks Used Available Capacity Mounted on"
    return f"{header}\n/dev/sda3 4700000000 280000000 {free_kb} 7% /target"


def reply(
    cmd: list[str],
    *,
    free_kb: int = FREE_KB,
    toc: str = TOC,
    containers: str = "c0ffee",
    running: str = "c0ffee",
    image: str = DB_IMAGE,
) -> Optional[str]:
    """The output a backup command gets, or ``None`` when the command is not one of the backup's.

    ``containers`` answers „which containers does the installation have" (``ps -a``), ``running`` „is its database
    running" (``ps`` of the database service).
    """
    if not cmd or cmd[0] != "docker":
        return None
    script = cmd[-1]
    if cmd[1] == "compose" and cmd[4] == "ps":
        return containers if "-a" in cmd else running
    if cmd[1] == "compose" and cmd[4] == "images":
        return image
    if cmd[1] == "run" and "--entrypoint" in cmd and cmd[cmd.index("--entrypoint") + 1] == "pg_restore":
        return toc
    if script == B._SIZE_SH:
        return str(DB_BYTES)
    if script == "df -Pk /target":
        return df_line(free_kb)
    if script == B._DUMP_SH:
        return ""
    if script.startswith("set -e; mv "):
        return f"{DUMP_BYTES}\n{SHA}"
    if script.startswith(("set -e; mkdir -p ", "cat ", "rm -f ", "cd ")):
        return ""
    return None

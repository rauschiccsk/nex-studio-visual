"""One-question Poradca container — the list of mounts IS the guarantee (``docs/specs/poradca.md`` §4.1).

The model is the isolated build (:mod:`backend.services.build_sandbox`), not the Konzultácia: a temporary home,
an unprivileged user without capabilities, a fenced network, login only via an environment variable. In addition to
the build, because Poradca only reads:

  * **Claude Code in restricted mode** (``--restricted --tools Read,Grep,Glob``): tools that run
    commands or code, ``WebFetch`` and ``WebSearch`` are not available; user and project settings
    are not loaded; file tools do not reach outside the working directory — not even via a symbolic link.
    Measured on 05.10.2026 with CLI 2.1.289: the tool list held only ``Glob``, ``Grep``, ``Read`` and Poradca's tools;
    reading ``/etc/hostname`` was refused both directly and via a link in the project;
  * **project read-only** — writes are refused by the kernel, not by a promise in the prompt;
  * **secret files overlaid with an empty file** (``.env*``, keys, certificates, …) and likewise every
    file with multiple hard links — a hard link is a second name for a file that may lie anywhere on the same
    disk, and restricted mode cannot tell it from a regular file;
  * ``.git`` overlaid with an empty ``tmpfs`` — history is served by the ``git_historia`` tool from the backend;
  * **no Knowledge Base** — it is served by a tool with the rights of the person asking; a mounted directory
    would bypass them;
  * **conversation transcript** in the directory of THIS conversation, mounted where Claude Code stores it according to
    pracovného priečinka — záznamy agenta stavby sa nepripájajú vôbec (nález B3);
  * **snímky obrazovky, ktoré Manažér priložil k otázkam TOHTO rozhovoru** (DEV-52) — len na čítanie, mimo
    projektu, a obmedzenému režimu povolené prepínačom ``--add-dir`` (bez neho Read mimo projektu odmietne;
    zmerané 10.10.2026 s CLI 2.1.294 oboma smermi). Iný rozhovor ani projekt sa k nim nedostane.

Tools "from behind" are served by the backend through a Unix socket valid for a single question (:mod:`.mcp_server`);
in the container they are relayed by :mod:`.shim`. The network stays fenced, as with the build.

If the container cannot start, the question fails with a reason — **never** falls back to running outside it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from backend.config.settings import settings
from backend.services import build_db, build_sandbox, sandbox_paths, secret_files
from backend.services.project_files import PRIVATE_DIR

logger = logging.getLogger(__name__)

_LABEL = "poradca sandbox"

#: Poradca's built-in tools — read only. Anything else makes restricted mode unavailable for it.
BUILTIN_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob")
#: MCP server name; tools are called in Claude Code as ``mcp__poradca__<tool>``.
MCP_SERVER_NAME = "poradca"

#: Where the container sees the directory with the socket of this question.
CONTAINER_RUN_DIR = "/run/poradca"
SOCKET_NAME = "mcp.sock"
#: DEV-52 — kde kontajner vidí priložené snímky svojho rozhovoru (len na čítanie, mimo projektu).
CONTAINER_ATTACHMENTS_DIR = "/run/poradca-prilohy"
#: stdio ↔ socket intermediary, in the backend image (Python standard library only).
SHIM_PATH = "/app/backend/services/poradca/shim.py"

_HOME = "/home/andros"
_CLAUDE_DIR = f"{_HOME}/.claude"
_CLAUDE_BIN_DIR = "/home/andros/.local"
_USER = "1000:1000"

#: Environment variables passed by NAME (the value does not go into argv, hence not into ``ps``). No GitHub token,
#: no database — Poradca writes nothing and the backend serves it "from behind".
_PASSTHROUGH_ENV: tuple[str, ...] = ("CLAUDE_CODE_OAUTH_TOKEN", "DISABLE_AUTOUPDATER", "LANG")

#: Names of files that carry secrets — the one rule of :mod:`backend.services.secret_files`; overlaid with an empty
#: file wherever in the project they lie (templates of an ``.env`` too — Poradca's stricter choice).
_SECRET_FILE_RE = secret_files.SECRET_FILE_RE

#: Characters that start a new field or record in a docker ``--mount`` spec (comma, quotes) — and control
#: characters. A file with such a name would smuggle its own ``source=`` into the overlay mount and docker would mount
#: an arbitrary host path under the project directory (finding of the independent review, 05.10.2026). Poradca
#: therefore never builds such a path — the question fails with the file name.
_MOUNT_UNSAFE = re.compile(r'[,"\x00-\x1f\x7f]')

#: The longest Unix socket path the Linux kernel accepts (``sun_path`` has 108 bytes including the null).
MAX_SOCKET_PATH = 107

#: More overlays than this is a finding, not an ordinary project — the question rather fails with a reason.
MAX_OVERLAYS = 2000

#: Memory and process cap of the container — a question that only reads does not need more.
_MEMORY = "2g"
_PIDS = "512"


class PoradcaUnavailable(RuntimeError):
    """The Poradca container cannot be started — the question fails with a reason, it never runs without it."""


def data_dir() -> Path:
    """Poradca directory on the host — the backend has it mounted at the same path (identity)."""
    return Path(settings.poradca_data_dir)


def session_dir(conversation_id: UUID) -> Path:
    return data_dir() / "sessions" / str(conversation_id)


def attachments_dir(conversation_id: UUID) -> Path:
    """DEV-52 — snímky, ktoré Manažér priložil k otázkam rozhovoru. Vedľa záznamu, nie v ňom: záznam má kontajner
    pripojený na zápis, snímky len na čítanie."""
    return data_dir() / "attachments" / str(conversation_id)


#: Čo vymazanie rozhovoru presúva do koša — záznam sedenia a priložené snímky, každé pod svojím menom.
_TRASHED = (("session", session_dir), ("attachments", attachments_dir))


def run_dir(token: str) -> Path:
    return data_dir() / "run" / token


def empty_file() -> Path:
    return data_dir() / "empty"


def trash_dir() -> Path:
    return data_dir() / "trash"


def move_to_trash(conversation_id: UUID) -> Optional[Path]:
    """Presunie záznam vymazaného rozhovoru a jeho priložené snímky do koša a vráti, kam; ``None``, keď nie je nič.

    ``os.rename`` v rámci jedného disku (kôš leží vedľa ``sessions/`` a ``attachments/``) je nedeliteľný: každá
    časť je buď celá preč, alebo celá na mieste — a keď druhá časť nejde, prvá sa vráti, takže rozhovor nikdy
    neostane napoly. Vymazanie to robí pred zmenou databázy a pri jej zlyhaní všetko vráti
    (:func:`restore_from_trash`); deleting the files themselves (:func:`discard`) comes only after, outside the lock.
    """
    present = [(name, where(conversation_id)) for name, where in _TRASHED if where(conversation_id).exists()]
    if not present:
        return None
    trash_dir().mkdir(parents=True, exist_ok=True)
    target = trash_dir() / f"{conversation_id}-{uuid4().hex}"
    target.mkdir()
    moved: list[tuple[str, Path]] = []
    try:
        for name, source in present:
            os.rename(source, target / name)
            moved.append((name, source))
    except OSError:
        for name, source in reversed(moved):
            os.rename(target / name, source)
        target.rmdir()
        raise
    return target


def restore_from_trash(trashed: Path, conversation_id: UUID) -> None:
    for name, where in _TRASHED:
        if (trashed / name).exists():
            os.rename(trashed / name, where(conversation_id))
    trashed.rmdir()


def discard(trashed: Path) -> None:
    """Deletes a record from the trash. What cannot be done (e.g. a container that survived a restart and still writes
    to it) is finished by :func:`sweep_trash` at the next start — the conversation is deleted either way."""
    shutil.rmtree(trashed, ignore_errors=True)
    if trashed.exists():
        logger.warning("%s: %s sa nepodarilo celý zmazať — dozmaže ho štart backendu", _LABEL, trashed)


def sweep_trash() -> int:
    """At backend start: removes what remained in the trash; returns how many records vanished. Never raises."""
    base = trash_dir()
    if not base.is_dir():
        return 0
    removed = 0
    for child in base.iterdir():
        shutil.rmtree(child, ignore_errors=True)
        removed += 0 if child.exists() else 1
    return removed


def container_name(project_slug: str, token: str) -> str:
    return f"nex-poradca-{project_slug}-{token}"


def network_name(project_slug: str, token: str) -> str:
    return f"nex-poradca-{project_slug}-{token}"


def project_dirs(project_slug: str) -> tuple[str, str]:
    """``(path in the container, path on the host)`` — slug verified, the path cannot escape ``/opt/projects``."""
    try:
        sandbox_paths.validate_project_slug(project_slug, sandbox=_LABEL)
        from backend.services.claude_agent import PROJECTS_ROOT

        container_dir = str(PROJECTS_ROOT / project_slug)
        prefixes: sandbox_paths.HostPrefixes = (("/opt/projects", "/opt/projects"),)
        host_dir = sandbox_paths.container_to_host(container_dir, prefixes, sandbox=_LABEL)
        sandbox_paths.assert_host_source_contained(host_dir, prefixes, sandbox=_LABEL)
    except sandbox_paths.SandboxPathError as exc:
        raise PoradcaUnavailable(str(exc)) from exc
    return container_dir, host_dir


def overlay_paths(project_dir: str) -> list[str]:
    """Paths in the project (relative) that are overlaid with an empty file.

    Súbor s menom tajomstva, každý obyčajný súbor s viac než jedným pevným odkazom a každý súbor v ``private/``
    (DEV-44 — čo Manažér priložil agentovi). Symbolické odkazy sa
    are not overlaid: restricted mode resolves them itself and refuses reading outside the project (measured), whereas
    overlaying a link would be resolved by docker in the container and could point it outside. ``.git`` is
    not traversed — it is overlaid entirely with an empty ``tmpfs``.

    Raises:
        PoradcaUnavailable: more overlays than :data:`MAX_OVERLAYS`.
    """
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(project_dir, followlinks=False):
        if dirpath == project_dir and ".git" in dirnames:
            dirnames.remove(".git")
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            rel = os.path.relpath(full, project_dir)
            # DEV-44: what the Manažér attached for the agent (a real e-mail with personal codes) — Poradca
            # sees the name, never the content, so the codes cannot end up quoted in the conversation.
            attached = rel.split(os.sep, 1)[0] == PRIVATE_DIR
            if _SECRET_FILE_RE.match(name) or st.st_nlink > 1 or attached:
                if _MOUNT_UNSAFE.search(rel):
                    raise PoradcaUnavailable(
                        f"{_LABEL}: súbor s tajomstvom alebo pevným odkazom má v ceste čiarku, úvodzovky či riadiaci "
                        f"znak ({rel!r}) — také meno sa nedá bezpečne prekryť. Premenuj ho, potom sa Poradca spustí."
                    )
                found.append(rel)
                if len(found) > MAX_OVERLAYS:
                    raise PoradcaUnavailable(
                        f"{_LABEL}: projekt má viac než {MAX_OVERLAYS} súborov na prekrytie (tajomstvá alebo "
                        "pevné odkazy) — to nie je bežný projekt; over ho skôr, než sa Poradca spustí"
                    )
    return sorted(found)


def prepare(conversation_id: UUID, token: str) -> tuple[Path, Path]:
    """Creates the conversation transcript directory and the socket directory of the question; returns their paths.

    The owner is the container user (1000), because Claude Code writes the transcript in it. The empty file for
    overlays is created once, read-only.

    Raises:
        PoradcaUnavailable: the Poradca directory is missing or cannot be written to.
    """
    base = data_dir()
    socket_path = run_dir(token) / SOCKET_NAME
    if len(str(socket_path).encode()) > MAX_SOCKET_PATH:
        raise PoradcaUnavailable(
            f"{_LABEL}: cesta socketu {socket_path} je dlhšia než {MAX_SOCKET_PATH} bajtov, ktoré unixový socket "
            "dovolí — priečinok Poradcu musí mať kratšiu cestu"
        )
    if not base.is_dir():
        raise PoradcaUnavailable(
            f"{_LABEL}: priečinok {base} neexistuje — v PROD compose kokpitu chýba jeho pripojenie "
            "(rovnaká cesta na hostiteľovi aj v backende)"
        )
    try:
        sessions = session_dir(conversation_id)
        sessions.mkdir(parents=True, exist_ok=True)
        run = run_dir(token)
        run.mkdir(parents=True, exist_ok=True)
        empty = empty_file()
        if not empty.exists():
            empty.write_bytes(b"")
        os.chmod(empty, 0o444)
        for path in (sessions, run):
            os.chown(path, 1000, 1000)
        for child in sessions.rglob("*"):
            os.lchown(child, 1000, 1000)
    except OSError as exc:
        raise PoradcaUnavailable(f"{_LABEL}: nedá sa pripraviť priečinok otázky ({exc})") from exc
    return sessions, run


@dataclass(frozen=True)
class ClaudeCall:
    """What is asked and with which model — everything in which two questions differ."""

    prompt: str
    claude_session_id: UUID
    #: A conversation's first question carries the charter and starts the session; later ones resume (``--resume``).
    charter_text: Optional[str]
    model: Optional[str]
    effort: Optional[str]
    #: DEV-52 — rozhovor má priložené snímky: ich priečinok sa pripojí a obmedzený režim ho smie čítať.
    attachments: bool = False


def claude_argv(call: ClaudeCall) -> list[str]:
    """``claude`` flags (without the program name — that is supplied by ``--entrypoint``)."""
    mcp = {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "type": "stdio",
                "command": "python3",
                "args": [SHIM_PATH, f"{CONTAINER_RUN_DIR}/{SOCKET_NAME}"],
            }
        }
    }
    args = [
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--restricted",
        "--tools",
        ",".join(BUILTIN_TOOLS),
        "--strict-mcp-config",
        "--mcp-config",
        json.dumps(mcp),
        "--allowedTools",
        f"mcp__{MCP_SERVER_NAME}",
        # ``--add-dir`` berie viac priečinkov — za ním preto vždy nasleduje ďalší prepínač, nikdy text otázky.
        *(["--add-dir", CONTAINER_ATTACHMENTS_DIR] if call.attachments else []),
        "--permission-mode",
        "dontAsk",
    ]
    if call.charter_text is not None:
        args += ["--session-id", str(call.claude_session_id), "--append-system-prompt", call.charter_text]
    else:
        args += ["--resume", str(call.claude_session_id)]
    if call.model:
        args += ["--model", call.model]
    if call.effort:
        args += ["--effort", call.effort]
    args.append(call.prompt)
    return args


def _assert_mount_safe(*parts: str) -> None:
    """Second safeguard: nothing that goes into ``--mount`` may carry a character that starts a new field in it."""
    for part in parts:
        if _MOUNT_UNSAFE.search(part):
            raise PoradcaUnavailable(f"{_LABEL}: cesta {part!r} sa nedá bezpečne pripojiť (čiarka/úvodzovky)")


def run_argv(
    *,
    project_slug: str,
    conversation_id: UUID,
    token: str,
    network: Optional[str],
    call: ClaudeCall,
    overlays: list[str],
) -> list[str]:
    """The whole ``docker run`` of one question. What is not here, Poradca does not see.

    PRESENT: temporary home, the transcript of THIS conversation, project read-only with overlays, ``.git`` as
    prázdny ``tmpfs``, binárka ``claude`` len na čítanie, priečinok socketu tejto otázky len na čítanie a — keď
    rozhovor nejaké má — priložené snímky TOHTO rozhovoru len na čítanie (DEV-52).
    ABSENT (guarded by a test): ``docker.sock``, ``/opt/customers``, ``/opt/uat``, ``/opt/infra``,
    Knowledge Base, access vault, shared ``~/.claude``, build agent transcripts.
    """
    container_dir, host_dir = project_dirs(project_slug)
    _assert_mount_safe(
        host_dir, container_dir, str(session_dir(conversation_id)), str(run_dir(token)), str(empty_file())
    )
    _assert_mount_safe(*overlays)
    container_session_dir = f"{_CLAUDE_DIR}/projects/{build_sandbox.session_dir_name(container_dir)}"
    argv = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name(project_slug, token),
        # Build marker: a container surviving a backend restart (the question keeps running, nobody reads it, it writes
        # to the conversation transcript) is cleaned up at start by ``build_db.reap_orphans`` — like its network.
        "--label",
        f"{build_db.OWNER_LABEL}=poradca",
        "--user",
        _USER,
        *(["--network", network] if network else []),
        "--no-healthcheck",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--memory",
        _MEMORY,
        "--pids-limit",
        _PIDS,
        "--mount",
        f"type=tmpfs,destination={_HOME},tmpfs-mode=0777",
        "--mount",
        "type=tmpfs,destination=/tmp,tmpfs-mode=1777",
        "--mount",
        f"type=bind,source={session_dir(conversation_id)},target={container_session_dir}",
        "--mount",
        f"type=bind,source={host_dir},target={container_dir},readonly",
    ]
    if os.path.isdir(os.path.join(host_dir, ".git")):
        argv += ["--mount", f"type=tmpfs,destination={container_dir}/.git,tmpfs-mode=0555"]
    for rel in overlays:
        argv += ["--mount", f"type=bind,source={empty_file()},target={container_dir}/{rel},readonly"]
    if call.attachments:
        attached = attachments_dir(conversation_id)
        _assert_mount_safe(str(attached))
        argv += ["--mount", f"type=bind,source={attached},target={CONTAINER_ATTACHMENTS_DIR},readonly"]
    argv += [
        "--mount",
        f"type=bind,source={_CLAUDE_BIN_DIR},target={_CLAUDE_BIN_DIR},readonly",
        "--mount",
        f"type=bind,source={run_dir(token)},target={CONTAINER_RUN_DIR},readonly",
        "-e",
        f"HOME={_HOME}",
        "-e",
        f"CLAUDE_CONFIG_DIR={_CLAUDE_DIR}",
    ]
    for name in _PASSTHROUGH_ENV:
        argv += ["-e", name]
    argv += ["-w", container_dir, "--entrypoint", "claude", build_sandbox.sandbox_image(), *claude_argv(call)]
    return argv

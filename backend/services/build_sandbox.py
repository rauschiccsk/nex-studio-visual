"""OS-isolated container for the BUILD turns of the Príprava and Návrh phases (ICCINT-16, STEP 1 of 2).

THE PROBLEM. A build turn is ``claude`` with an unrestricted Bash/Write/Edit profile, and today it runs as
a subprocess OF THE BACKEND — inside a container that bind-mounts ``/opt/customers`` read-write, ``/opt/uat``
read-write, ``/opt/infra``, ``/home/icc/knowledge`` read-write and ``/var/run/docker.sock``, all as root. So
a build for one customer can read and overwrite another customer's PRODUCTION files, and nothing but the
agent's own judgement stands in the way. The threat model is NOT an attacker: a container holding the docker
socket is reachable by a determined one no matter what we mount, and pretending otherwise would be theatre.
The threat model is the MISTAKE of an unsupervised agent — the same class of mistake that, during a manual
tidy-up of ``/opt``, moved directories without checking what referenced them and took down every backup.

THE GUARANTEE IS THE MOUNT LIST — AND THE MOUNT LIST IS NOT ONLY ABOUT ``/opt``. :mod:`consult_sandbox`
states the lesson this module inherits: *"a deny-list cannot be completed … only OS isolation is robust."*
A container that has never been shown ``/opt/customers`` cannot write to ``/opt/customers``. The first
version of this module stopped there and was WRONG, because a sandbox does not need a path to a victim if it
can write a file that a LATER, PRIVILEGED turn executes. Two such files were demonstrated by audit:

  * ``/home/andros/.claude`` was mounted WRITABLE and shared with the root backend container. It holds
    ``settings.json`` (``defaultMode: bypassPermissions`` + ``UserPromptSubmit`` / ``PostToolUse`` / ``Stop``
    hooks) and ``hooks/wip-recorder.sh``, all owned by uid 1000 — the sandbox's own uid. Poison a hook in a
    Návrh turn, and the next Programovanie turn (root, docker socket, ``/opt/customers`` rw) runs it. So did
    every interactive session on the host. The same mount also exposed 22 other projects' session
    transcripts and a 1.9 MB ``history.jsonl`` — i.e. it handed back through the config dir exactly the
    customer content the ``/opt`` mount list had just withheld.
  * ``/opt/projects/<slug>/.claude/settings.json`` — the *project* setting source — carries ``PostToolUse``
    hooks whose commands live in ``<slug>/scripts/``, and ``.claude/agents/<role>/settings.json`` is the
    deny profile that is the only thing surviving ``bypassPermissions``. All of it sat in the deliberately
    read-write project mount, so a sandboxed turn could rewrite what a later privileged turn would execute,
    or delete its own restraints.

BOTH ARE CLOSED HERE, structurally, by the two mechanisms the audit named — take the file out of the mount,
or re-deploy it from a trusted source on every dispatch:

  * HOME is an EPHEMERAL ``tmpfs``. The shared ``~/.claude`` is gone from the sandbox entirely; auth is the
    ``CLAUDE_CODE_OAUTH_TOKEN`` env (Claude MAX subscription — never the direct Anthropic API), and the only
    thing bind-mounted back into it is THIS project's own transcript directory, so ``--resume`` works and no
    other project's history is reachable. Permission auto-approval no longer rides on a writable user
    ``settings.json`` at all: it is the ``--permission-mode bypassPermissions`` FLAG, which lives in the
    argv and cannot be edited from inside the container.
  * every project file that decides what a later turn EXECUTES — ``.claude/settings.json``,
    ``.claude/settings.local.json``, ``.claude/agents/``, ``.claude/skills/``, ``.mcp.json``, and every hook
    command those settings point at inside the project — is re-mounted READ-ONLY on top of the read-write
    project bind. The kernel then refuses both the write ("Read-only file system") and the delete-and-recreate
    ("Device or resource busy"). What the sandbox may still create is the two source files it cannot make
    LOAD: ``settings.local.json`` is excluded by ``--setting-sources user,project`` and ``.mcp.json`` by
    ``--strict-mcp-config``, both passed on EVERY turn, sandboxed or not (:func:`claude_agent.build_claude_argv`).

WHAT REMAINS TRUE AND MUST NOT BE OVERSTATED. A sandboxed turn can still write ordinary project source, and
a later Programovanie turn runs project source as root — but that turn WRITES that source as root anyway, so
the sandbox grants no capability there; the pipeline's trust in its own repository is a property of the
pipeline, not a hole this module opened. The network is NOT restricted (see :data:`NETWORK_RESIDUAL`).

WHAT THIS IS NOT — SAY IT OUT LOUD. This is STEP 1 of two, and step 1 is not the fix; it is the half of the
fix that can ship without breaking anything. Only the **Príprava** and **Návrh** turns move in here
(:data:`SANDBOXED_PHASES`), because those two write documents. **Programovanie, Vizuál and Verifikácia keep
running as backend subprocesses, with the docker socket and every one of those host mounts**, because the
agent charter orders them to run ``docker compose up`` and to test against a real PostgreSQL — take the
socket away today and those phases simply stop working. Brokered access to Docker is STEP 2. Until step 2
lands, the exposure this module removes is removed for two phases out of five and for no others. Anyone
reading a summary that says "build turns are now isolated" is reading something untrue.

THE KNOWLEDGE BASE IS READABLE, NOT WRITABLE (Director decision, 23.08.2026). The AI Agent charter §3 gives
the agent three knowledge levels. An earlier version of this sandbox took two of them away; the Director's
decision was *"pripoj znalostnú bázu read-only"*, and BOTH halves of "read" are implemented — a half-working
§3 would be the same trap in a smaller size:

  1. **§3(1) direct reads** — ``/home/icc/knowledge`` is bind-mounted ``readonly``. ``Read``/``Grep`` work;
     a write is kernel-refused (EROFS).
  2. **§3(1) the RAG path** (*"Prístup: RAG (Qdrant + Ollama embeddings) + priame čítanie súborov"*) — the
     project's own ``scripts/rag_query.py`` talks to Qdrant directly, so mounting files alone would NOT have
     revived it. ``QDRANT_URL`` and ``OLLAMA_URL`` are therefore in :data:`_PASSTHROUGH_ENV`; both point at
     the docker bridge gateway, which the sandbox reaches on the default bridge.
  3. **§3(3) deliberate writes into the shared KB + reindex** — DELIBERATELY still unavailable: read-only is
     what was decided. The agent must not discover this by having a write fail mid-build, so the charter
     §3 says outright that in Príprava and Návrh the shared KB is read-only and a contribution waits for a
     later phase. A rule the agent cannot see is a trap, not a rule.

§3(2), the per-project ``MEMORY.md``, is in the project tree and was never affected.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from backend.config.settings import settings
from backend.services import sandbox_paths
from backend.services.claude_agent import ClaudeAgentError

logger = logging.getLogger(__name__)

#: This module's label in the shared path guards' error messages.
_SANDBOX_LABEL = "build sandbox"

#: The phases whose build turns run in here. Príprava and Návrh produce DOCUMENTS (the Zadanie/Špecifikácia
#: dialogue, the design doc, the task plan) — no ``docker compose up``, no real PostgreSQL, nothing that
#: needs the host — which is precisely why they are the two that can move first. The routing keys on THIS
#: value, i.e. on the phase the engine is already in, so nothing has to be remembered or configured per
#: dispatch: a phase cannot be forgotten the way a per-call ``sandbox=True`` flag can.
SANDBOXED_PHASES: tuple[str, ...] = ("priprava", "navrh")

#: ``HOME`` inside the sandbox — an EPHEMERAL tmpfs, not a bind. Set explicitly because the container runs
#: under a NUMERIC uid with no ``/etc/passwd`` entry, so ``HOME`` would otherwise be unset or inherited from
#: the backend (``/root``, which the unprivileged user cannot write).
_SANDBOX_HOME = "/home/andros"

#: The Claude config dir INSIDE the sandbox. Same path as on the host, so ``--resume``, the transcript
#: layout and every log line read identically — but it lives on the tmpfs, so everything the CLI writes
#: there (``.claude.json``, caches, shell snapshots) dies with the container. The audited version bind-
#: mounted the HOST's ``/home/andros/.claude`` here, writable: that single mount carried the user
#: ``settings.json`` with its hooks, the hook scripts themselves, 22 other projects' transcripts and
#: ``history.jsonl``. Only ONE thing from it is genuinely needed by a turn and it is mounted back
#: individually — see :func:`session_dir_name`.
_SANDBOX_CLAUDE_DIR = f"{_SANDBOX_HOME}/.claude"

#: tmpfs mode for HOME. World-writable because the container has exactly one (numeric, unprivileged) user
#: and no ``/etc/passwd`` entry for it; docker cannot set the tmpfs OWNER, only its mode.
_HOME_TMPFS_MODE = "0777"

#: HOST location of the shared Claude config dir. NOT mounted into the sandbox — it is read/written by the
#: BACKEND (which does mount it) so that :func:`prepare_turn` can create and chown the one per-project
#: transcript directory the sandbox gets. Kept as a module global (not a literal) so tests can point it at a
#: tmp dir and never touch the real one.
_CLAUDE_HOME_DIR = "/home/andros/.claude"

#: The host's ``claude`` binary, mounted READ-ONLY. The backend image deliberately does not bundle claude
#: (see the root ``Dockerfile``): the host build is mounted from here and symlinked onto ``PATH`` as
#: ``/usr/local/bin/claude``, so the deployed agent is byte-identical to the team's. A sibling of that image
#: without this mount has a dangling symlink and ``--entrypoint claude`` fails instantly. Read-only: the
#: sandbox must run the binary, never replace it.
_CLAUDE_BIN_DIR = "/home/andros/.local"

#: Unprivileged, and equal to the host owner of ``/opt/projects`` (``andros`` = uid/gid 1000), so files the
#: build writes are owned by the same user that owns the tree — never root-owned artefacts left behind.
#: NUMERIC on purpose: this backend image has no ``andros`` account (its Dockerfile documents the choice to
#: run as root), so ``--user andros`` would fail to start the container outright.
_SANDBOX_UID = 1000
_SANDBOX_GID = 1000
_SANDBOX_USER = f"{_SANDBOX_UID}:{_SANDBOX_GID}"

#: The permission mode the sandboxed build turn is launched with, as an ARGV FLAG. Until now auto-approval
#: came from ``defaultMode: bypassPermissions`` inside the mounted user ``settings.json`` — i.e. the turn's
#: own permission posture was a file the turn could rewrite. In argv it cannot be. Bypassing prompts is also
#: the correct posture *here* specifically, and only here: the CLI's own help recommends it for sandboxes,
#: and the role's ``deny`` profile still arrives via ``--settings`` (a deny survives ``bypassPermissions`` —
#: verified against the real CLI) from a directory this module mounts READ-ONLY.
SANDBOX_PERMISSION_MODE = "bypassPermissions"

#: In-container project dir → HOST path for the ``--mount`` source. A sibling ``docker run`` is resolved by
#: the daemon on the HOST, so the source must be a host path. On this deployment ``/opt/projects`` is bind-
#: mounted IDENTICALLY into the backend (verified via ``docker inspect`` on the running prod backend:
#: ``bind /opt/projects -> /opt/projects``), so the translation is the identity — the same fact
#: :mod:`vizual_sandbox` records. It is written as a table rather than assumed, so the day the deployment
#: changes there is one line to change and a containment assert that still holds.
_CONTAINER_TO_HOST_PREFIX: sandbox_paths.HostPrefixes = (("/opt/projects", "/opt/projects"),)

#: Project-relative paths that decide what a LATER, PRIVILEGED turn executes, and are therefore re-mounted
#: READ-ONLY on top of the read-write project bind (each only when it actually exists). This is the
#: "re-deploy from a trusted source at every dispatch" half of the audit's remedy: the file stays where the
#: CLI expects it, but the sandbox gets the kernel's answer instead of a write.
#:
#:   * ``.claude/settings.json`` — the *project* setting source; Create Project writes ``PostToolUse`` hooks
#:     into it (RAG reindex, Telegram notify) that run as SHELL COMMANDS in every later, root turn;
#:   * ``.claude/settings.local.json`` — the *local* source, same power (also excluded by
#:     ``--setting-sources user,project``, so this is belt AND suspenders);
#:   * ``.claude/agents/`` — both role charters AND the ``deny`` profiles passed via ``--settings``. The
#:     charter forbids the agent to rewrite its own charter; this makes the prohibition enforceable;
#:   * ``.claude/skills/`` — project skills are loaded by name into a later turn;
#:   * ``.mcp.json`` — MCP servers are SPAWNED PROCESSES (also neutralised by ``--strict-mcp-config``).
#:
#: Hook command scripts live outside this list (they are ordinary files under ``scripts/``) and are
#: discovered from the settings themselves — see :func:`_hook_command_paths`.
_FROZEN_PROJECT_CONFIG: tuple[str, ...] = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/agents",
    ".claude/skills",
    ".mcp.json",
)

#: The settings files whose ``hooks`` blocks are parsed for in-project command paths to freeze.
_HOOK_SETTINGS_FILES: tuple[str, ...] = (".claude/settings.json", ".claude/settings.local.json")

#: Environment variables handed to the sandboxed turn BY NAME (``-e NAME``, no value). docker then copies
#: the value from the CLI process's own environment, so a secret never appears in the ``docker run`` argv —
#: which any process on the host can read out of ``ps``. This is an ALLOW-LIST, not a filter: everything the
#: in-process turn used to inherit — ``DATABASE_URL``, ``SECRET_KEY``, ``TEST_DATABASE_URL``, the credential
#: store path, the Dedo channel, Qdrant/Ollama — is simply not passed. A document-writing turn needs none of
#: it, and what is not passed cannot be printed by an agent with a Bash tool.
#:
#: ``CLAUDE_CODE_OAUTH_TOKEN`` is now the ONLY way the sandbox authenticates: the ``~/.claude`` bind that
#: used to carry ``.credentials.json`` is gone. :func:`preflight` therefore fails readiness when it is
#: unset, rather than letting every Príprava turn discover it one at a time.
_PASSTHROUGH_ENV: tuple[str, ...] = (
    "CLAUDE_CODE_OAUTH_TOKEN",  # Claude MAX subscription auth (ICC never uses the direct Anthropic API)
    "IS_SANDBOX",  # parity with the backend container: how the CLI already runs today
    "DISABLE_AUTOUPDATER",  # parity: the CLI must not self-update mid-turn
    "GITHUB_TOKEN",  # the agent commits and pushes its own work
    "GH_TOKEN",
    "LANG",
    # Charter §3(1) reaches the KB two ways and the file mount only revives one of them: the project's own
    # ``scripts/rag_query.py`` talks to Qdrant directly. Both URLs point at the docker bridge gateway, which
    # the sandbox reaches on the default bridge. Not secrets — plain service addresses.
    "QDRANT_URL",
    "OLLAMA_URL",
)

#: The shared ICC knowledge base, mounted read-only (charter §3(1)). Same path inside and out.
_KB_DIR = "/home/icc/knowledge"

#: Git identity for the sandbox, passed BY VALUE (it is not a secret) as the env vars git itself honours.
#:
#: WHY NOT MOUNT A ``.gitconfig``. Today's in-process turn takes its identity from ``/root/.gitconfig``
#: baked into the backend image (``NEX Studio <studio@isnex.eu>``, ``safe.directory=*``); the sandbox sets
#: ``HOME=/home/andros``, where that file does not exist, so ``git config --global --list`` failed with
#: *"unable to read config file"* and every ``git commit`` the agent made itself died on *"Author identity
#: unknown"* — while ``GITHUB_TOKEN``/``GH_TOKEN`` were being passed in with the comment "the agent commits
#: and pushes its own work". Mounting the HOST's ``/home/andros/.gitconfig`` would fix the symptom but is
#: the wrong source twice over: the backend container does not mount it, so this module could neither stat
#: nor verify what it was about to hand over, and that file carries the host user's ``gh`` credential
#: helpers — host identity leaking into the sandbox is the direction we are closing, not opening. The env
#: vars are self-contained, need no host file to exist, and reproduce the in-process identity exactly.
#: ``safe.directory=*`` is carried through git's own ``GIT_CONFIG_*`` env protocol for parity with the
#: image's gitconfig (:func:`prepare_turn` chowns the tree to the sandbox uid, so it should never be needed
#: — it is here so a tree the chown could not reach fails on permissions, honestly, and not on a confusing
#: "dubious ownership" error).
_GIT_IDENTITY_ENV: tuple[tuple[str, str], ...] = (
    ("GIT_AUTHOR_NAME", "NEX Studio"),
    ("GIT_AUTHOR_EMAIL", "studio@isnex.eu"),
    ("GIT_COMMITTER_NAME", "NEX Studio"),
    ("GIT_COMMITTER_EMAIL", "studio@isnex.eu"),
    ("GIT_CONFIG_COUNT", "1"),
    ("GIT_CONFIG_KEY_0", "safe.directory"),
    ("GIT_CONFIG_VALUE_0", "*"),
)

#: Default image repository for the sandbox. It is a sibling of THIS backend image (it needs the same git /
#: gh / node tooling and the same ``claude`` symlink), tagged with the running ``APP_VERSION``. Deriving the
#: default from the version rather than pinning a literal is the fix for the failure :mod:`consult_sandbox`
#: was audited for: its default names a tag that does not exist on this host, so every Konzultácia silently
#: lost its guarantee. ``BUILD_SANDBOX_IMAGE`` overrides.
_DEFAULT_IMAGE_REPO = "nex-studio-visual-backend"

#: HONEST RECORD OF WHAT IS **NOT** ISOLATED — the network. Audit measured it from inside the container:
#: the default bridge gateway reaches the host, and through it this Studio's own API (9217), the v3 API
#: (9207) and Traefik (80/443), i.e. every deployed customer app and every UAT. Postgres, Ollama and Qdrant
#: were NOT reachable. There is no docker-native way to say "internet yes, host no": ``--network none`` cuts
#: the Anthropic API too (measured — the turn hangs until its timeout and produces nothing), ``--internal``
#: cuts all egress, and a user-defined bridge still has a gateway to the host. Restricting this needs a host
#: firewall rule in the ``DOCKER-USER`` chain (drop new connections from the sandbox subnet to the host),
#: which is an infrastructure change outside this module and outside STEP 1. Until it exists, the sentence
#: "the sandbox sees its own project and its own login" is true of the FILESYSTEM only, and this constant is
#: logged once per process so nobody has to infer that from silence.
NETWORK_RESIDUAL = (
    "build sandbox runs on the default docker bridge: outbound internet is REQUIRED (the Claude MAX "
    "endpoint; --network none was measured to kill the turn), and the host gateway therefore stays "
    "reachable — the Studio API (9217), the v3 API (9207) and Traefik (80/443) answer from inside the "
    "sandbox. The filesystem isolation is unaffected; restricting this needs a DOCKER-USER firewall rule on "
    "the host and is NOT implemented (ICCINT-16 STEP 1)."
)
_network_residual_logged = False

#: stderr signatures meaning the SANDBOX itself could not run (docker CLI/daemon/image/mount problem) — as
#: opposed to ``claude`` running inside a healthy sandbox and failing. The distinction decides which error
#: type the turn raises, and therefore what the operator is told.
_UNAVAILABLE_RE = re.compile(
    r"(cannot connect to the docker daemon"
    r"|is the docker daemon running"
    r"|permission denied while trying to connect to the docker daemon"
    r"|/var/run/docker\.sock"
    r"|unable to find image"
    r"|no such image"
    r"|pull access denied"
    r"|bind source path does not exist"
    r"|invalid mount config)",
    re.IGNORECASE,
)

#: Appended to every unavailability error. An operator who cannot start the sandbox has exactly two correct
#: moves, and the message names both rather than leaving them to be guessed.
_REMEDIATION = (
    "Build the sandbox image (or point BUILD_SANDBOX_IMAGE at an existing tag). If builds must not wait, "
    "set BUILD_SANDBOX=0 — that is a DELIBERATE choice to run Príprava/Návrh as backend subprocesses again, "
    "with /opt/customers, /opt/uat, /opt/infra and the docker socket in reach."
)

#: Values of ``BUILD_SANDBOX`` that switch the isolation OFF. The empty string is deliberately NOT among
#: them: ``BUILD_SANDBOX=`` means "mentioned but not set", which the compose idioms ``BUILD_SANDBOX=${BUILD_SANDBOX}``
#: (unexported variable) and ``environment: - BUILD_SANDBOX`` both produce, and a variable somebody ADDS to a
#: config and leaves blank must never be the one that disables a security boundary. Unset and empty are the
#: same statement and both mean ON.
_OFF_VALUES: tuple[str, ...] = ("0", "false", "no", "off")


class BuildSandboxUnavailable(ClaudeAgentError):
    """The build sandbox could not be launched (no docker CLI, daemon unreachable, image or bind absent).

    THIS IS RAISED, NOT SWALLOWED — the deliberate difference from :class:`consult_sandbox.SidecarUnavailable`.
    A Konzultácia that loses its sidecar degrades to an in-process turn that is still read-only by tool
    profile, so the fallback costs defence-in-depth and nothing else. A BUILD turn that loses its sandbox
    degrades to an unrestricted Bash tool inside a container mounting every customer's production tree: the
    fallback IS the exposure this module exists to remove, so taking it automatically would quietly undo the
    change while every surface reported success. The turn therefore fails, loudly and with remediation, and
    :func:`sandbox_enabled` is the one way back to a subprocess — a switch somebody has to throw on purpose.

    A ``ClaudeAgentError`` subclass so the orchestrator's existing failure handling reports it as a failed
    turn rather than an uncaught exception. Its message does not match the transient-retry pattern, so the
    turn fails fast instead of retrying a precondition that cannot fix itself.
    """


def sandbox_enabled() -> bool:
    """Whether Príprava/Návrh turns route through the sandbox. Default ON — and EMPTY still means ON.

    ``BUILD_SANDBOX=0``/``false``/``no``/``off`` sends them back to the in-process subprocess. Read from the
    environment at TURN time, not cached at import, so the switch takes effect without restarting the
    backend — when a build must not wait for a fix, that is the lever, and throwing it is a visible act
    rather than a silent fallback. See :data:`_OFF_VALUES` for why an empty value is not a switch-off.
    """
    return os.environ.get("BUILD_SANDBOX", "1").strip().lower() not in _OFF_VALUES


def phase_uses_sandbox(stage: Optional[str]) -> bool:
    """Whether ``stage`` is one of the phases whose build turns are isolated (:data:`SANDBOXED_PHASES`).

    Unknown / missing phase → ``False``: an unrecognised caller keeps today's behaviour instead of being
    silently routed somewhere it was never tested. Programovanie, Vizuál and Verifikácia answer ``False``
    by construction, which is what keeps STEP 1 from breaking the phases that still need Docker.

    A ``False`` here is only ever safe when every dispatch actually PASSES its phase. It did not: the audit
    found ``_plan_pass_once`` — the turn behind every task-plan pass in BOTH Príprava and Návrh — omitting
    ``stage=``, so this function answered ``False`` for a phase that is in the list and the whole generation
    ran unisolated with nothing logged. ``tests/test_build_sandbox.py`` now walks the orchestrator's AST and
    fails if any ``invoke_claude`` call site omits the argument, because a default that means "keep the
    dangerous behaviour" needs the call sites checked by a machine, not by reading.
    """
    return isinstance(stage, str) and stage.strip().lower() in SANDBOXED_PHASES


def sandbox_image() -> str:
    """The image the sandbox runs — ``BUILD_SANDBOX_IMAGE`` if set, else this backend's own version tag."""
    return settings.build_sandbox_image or f"{_DEFAULT_IMAGE_REPO}:v{settings.app_version}"


def container_name() -> str:
    """A unique, greppable name per turn, so a container left behind by a timeout can be found and reaped."""
    return f"nex-build-{uuid4().hex[:16]}"


def looks_unavailable(stderr_text: str) -> bool:
    """Whether ``stderr`` says the SANDBOX could not start (vs. ``claude`` failing inside a healthy one)."""
    return bool(_UNAVAILABLE_RE.search(stderr_text or ""))


def unavailable(detail: str) -> BuildSandboxUnavailable:
    """Build the loud, actionable unavailability error (never a quiet downgrade)."""
    return BuildSandboxUnavailable(f"{_SANDBOX_LABEL} could not start: {detail} — {_REMEDIATION}")


# --------------------------------------------------------------------------------------------------------
# The one piece of ``~/.claude`` a turn genuinely needs: its own session transcript
# --------------------------------------------------------------------------------------------------------


def session_dir_name(container_project_dir: str) -> str:
    """The ``~/.claude/projects/<name>`` directory ``claude`` keeps THIS project's transcripts in.

    The CLI derives it from the cwd by replacing every non-alphanumeric character with ``-``, so
    ``/opt/projects/acme`` → ``-opt-projects-acme``. Verified against the live host (``-opt-projects-nex-
    productcatalogs``, ``-opt-emcenter-web-src``, …) AND end-to-end in a real sandboxed turn: a first turn
    created its transcript inside exactly this directory and a second turn ``--resume``d it with nothing
    else from ``~/.claude`` mounted.

    Binding this ONE directory instead of the whole config dir is what makes the tmpfs HOME possible: the
    sandbox keeps ``--resume`` and loses 22 other projects' transcripts and ``history.jsonl`` with it.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", container_project_dir)


def _host_session_dir(container_project_dir: str) -> str:
    """HOST path of this project's transcript directory (created/chowned by :func:`prepare_turn`)."""
    return os.path.join(_CLAUDE_HOME_DIR, "projects", session_dir_name(container_project_dir))


# --------------------------------------------------------------------------------------------------------
# Per-turn preparation — ownership. The sandbox is uid 1000; the in-process turns are root.
# --------------------------------------------------------------------------------------------------------


def _chown_tree_sync(root: str, *, uid: int, gid: int) -> tuple[int, list[str]]:
    """``chown -R`` in pure Python; returns ``(changed, paths_it_could_not_change)``.

    Symlinks are chowned with ``lchown`` (never followed — following one would let a link inside the project
    redirect the chown at an arbitrary host path).

    A single failure never aborts the walk, but it is NOT swallowed either: the caller reports it. Changing
    the owner of somebody else's file needs ``CAP_CHOWN``, which the backend container has (it runs as root)
    — a deployment where it does not is one where the sandbox is about to fail on file permissions, and the
    operator has to hear that from the preparation step rather than from a confused agent.
    """
    changed = 0
    failed: list[str] = []

    def _one(path: str) -> None:
        nonlocal changed
        try:
            st = os.lstat(path)
            if st.st_uid == uid and st.st_gid == gid:
                return
            os.lchown(path, uid, gid)
            changed += 1
        except OSError as exc:  # a single unreachable path must not abort the whole preparation
            logger.debug("build sandbox: could not chown %s (%s)", path, exc)
            failed.append(path)

    _one(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            _one(os.path.join(dirpath, name))
    return changed, failed


async def prepare_turn(project_slug: str) -> None:
    """Make the project and its transcript directory OWNED BY the sandbox uid, before the container starts.

    WHY THIS EXISTS AT ALL. Every turn so far ran as ROOT inside the backend container, so the artefacts it
    left behind are root-owned: ``~/.claude/projects/-opt-projects-<slug>/*.jsonl`` (mode 0600, so uid 1000
    cannot even READ it) and, on live projects, files inside the project itself — audit found
    ``nex-productcatalogs/docs/specs/versions/v0.1.0/specification.md`` root:644 and
    ``.claude/wip-actions.log`` root:644. The sandbox runs as uid 1000. Without this step:

      * ``--resume`` of a warm session dies with *"No conversation found with session ID"* — reproduced
        live, and a fresh session in the same root-owned directory returned an answer while silently
        writing NO transcript, so the NEXT turn fails again;
      * the ``Write``/``Edit`` tools hit ``EACCES`` on exactly the Špecifikácia the phase exists to update
        (in-place ``open(path,"w")`` fails; only delete-and-recreate works, because the parent dir is
        writable) — so the agent either reports a framework issue or works around it with ``rm``.

    It is deliberately AUTOMATIC and per-turn rather than a deployment migration script: the mixed-ownership
    state does not only come from the past, it is RE-CREATED whenever a later phase (Programovanie, Vizuál,
    Verifikácia — still root, by design, until STEP 2) writes into the same session directory or project.
    A migration would fix the projects that exist today and be wrong again by the next Programovanie turn.

    Creating the transcript directory is part of the job: ``--mount`` (correctly) REFUSES a source that does
    not exist, so a brand-new project would otherwise report the sandbox as unavailable on its very first
    turn.

    Raises:
        BuildSandboxUnavailable: the transcript directory could not be created — the sandbox cannot start
            without it, and a turn that cannot establish its boundary must fail loudly, never fall back.
    """
    from backend.services.claude_agent import PROJECTS_ROOT

    # The slug is validated HERE too, not only in :func:`build_run_argv`: preparation runs FIRST, and it
    # creates directories and changes ownership. A ``..`` reaching this far would compose a nonsense
    # transcript directory before the mount composer ever got the chance to refuse it.
    try:
        sandbox_paths.validate_project_slug(project_slug, sandbox=_SANDBOX_LABEL)
    except sandbox_paths.SandboxPathError as exc:
        raise BuildSandboxUnavailable(str(exc)) from exc

    # The chown targets are the BACKEND's own view of the paths (that is what this process can reach); the
    # host translation is only ever needed for a ``--mount`` source, which the daemon resolves.
    container_project_dir = str(PROJECTS_ROOT / project_slug)
    session_dir = _host_session_dir(container_project_dir)

    def _prepare() -> tuple[int, list[str]]:
        try:
            os.makedirs(session_dir, exist_ok=True)
        except OSError as exc:
            raise unavailable(f"cannot create the session transcript dir {session_dir} ({exc})") from exc
        sessions, session_failures = _chown_tree_sync(session_dir, uid=_SANDBOX_UID, gid=_SANDBOX_GID)
        # The project tree may legitimately be absent (a dry-run workspace); the mount then fails and is
        # classified as unavailable, which is the honest answer — do not invent a directory here.
        project, project_failures = (
            _chown_tree_sync(container_project_dir, uid=_SANDBOX_UID, gid=_SANDBOX_GID)
            if os.path.isdir(container_project_dir)
            else (0, [])
        )
        return sessions + project, session_failures + project_failures

    # os.walk over a project tree is blocking I/O; keep it off the event loop.
    changed, failures = await asyncio.to_thread(_prepare)
    if changed:
        logger.info(
            "build sandbox: re-owned %d path(s) to %s for %s — root-owned leftovers from the non-sandboxed "
            "phases are unreadable/unwritable to the sandbox uid",
            changed,
            _SANDBOX_USER,
            project_slug,
        )
    if failures:
        # Not fatal (the turn may never touch those files), but never silent: this is the exact shape of
        # the failure the step exists to prevent, and the agent's error message for it ("No conversation
        # found", "Permission denied") names none of the cause.
        logger.warning(
            "build sandbox: %d path(s) for %s could NOT be re-owned to %s (e.g. %s) — a sandboxed turn that "
            "touches them will fail on permissions, and --resume will fail if a transcript is among them. "
            "The backend needs CAP_CHOWN (it runs as root in the deployed container) for this to work.",
            len(failures),
            project_slug,
            _SANDBOX_USER,
            ", ".join(failures[:3]),
        )


# --------------------------------------------------------------------------------------------------------
# The argv — the mount list IS the guarantee
# --------------------------------------------------------------------------------------------------------


def _hook_command_paths(host_project_dir: str) -> list[str]:
    """Project-relative paths of every hook COMMAND the project settings point at inside the project.

    A frozen ``settings.json`` is worth nothing if the script it names stays writable: the agent would just
    edit ``scripts/hook_rag_reindex.sh`` instead, and the next root turn would run the edit. The command is
    a shell string (``/opt/projects/<slug>/scripts/hook_rag_reindex.sh``, possibly with arguments), so the
    first token is taken with :func:`shlex.split`; a command that resolves OUTSIDE the project is ignored,
    because the sandbox cannot reach it in the first place.

    Malformed or unreadable settings are ignored rather than fatal — a project with a broken settings file
    is a project problem, and refusing to start the sandbox over it would be the wrong trade.
    """
    found: list[str] = []
    for rel_settings in _HOOK_SETTINGS_FILES:
        path = os.path.join(host_project_dir, rel_settings)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        hooks = data.get("hooks") if isinstance(data, dict) else None
        if not isinstance(hooks, dict):
            continue
        for matchers in hooks.values():
            if not isinstance(matchers, list):
                continue
            for matcher in matchers:
                entries = matcher.get("hooks") if isinstance(matcher, dict) else None
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict) or entry.get("type") != "command":
                        continue
                    command = entry.get("command")
                    if not isinstance(command, str) or not command.strip():
                        continue
                    try:
                        first = shlex.split(command)[0]
                    except (ValueError, IndexError):
                        continue
                    candidate = first if os.path.isabs(first) else os.path.join(host_project_dir, first)
                    real = os.path.realpath(candidate)
                    root = os.path.realpath(host_project_dir)
                    if not real.startswith(root + os.sep) or not os.path.isfile(real):
                        continue
                    rel = os.path.relpath(real, root)
                    if rel not in found:
                        found.append(rel)
    return found


def frozen_project_paths(host_project_dir: str) -> list[str]:
    """Every project-relative path re-mounted READ-ONLY over the read-write project bind, in a stable order.

    Only paths that EXIST are returned: ``--mount`` refuses an absent source, and inventing the file would
    write into the customer's repository. The two that can therefore be CREATED by a sandboxed turn —
    ``settings.local.json`` and ``.mcp.json`` — are neutralised on the loading side instead, by the
    ``--setting-sources user,project`` / ``--strict-mcp-config`` flags every turn carries.

    Entries already contained in a frozen DIRECTORY (e.g. a hook script under ``.claude/agents/``) are
    dropped: docker would accept the nested mount, but a mount list with redundant entries is a mount list
    nobody can check by eye.
    """
    present = [rel for rel in _FROZEN_PROJECT_CONFIG if os.path.exists(os.path.join(host_project_dir, rel))]
    dirs = [rel for rel in present if os.path.isdir(os.path.join(host_project_dir, rel))]
    for rel in _hook_command_paths(host_project_dir):
        if any(rel == d or rel.startswith(d + os.sep) for d in dirs):
            continue
        if rel not in present:
            present.append(rel)
    return present


def build_run_argv(*, project_slug: str, container_name: str, claude_argv: list[str]) -> list[str]:
    """Compose the EXACT ``docker run`` argv for a sandboxed build turn. The mount list IS the guarantee.

    ``claude_argv`` is the full ``["claude", …]`` from :func:`claude_agent.build_claude_argv`; the leading
    ``"claude"`` is dropped because ``--entrypoint claude`` supplies it, and the rest is appended after the
    image. The per-turn flags are therefore byte-identical to the in-process turn — sandbox and subprocess
    differ in transport and in nothing else, including streaming.

    PRESENT, and each for a reason:
      * ``--rm`` + ``--name`` — ephemeral, and findable so a hung container can be killed and reaped;
      * ``--user 1000:1000`` — unprivileged, never root, and the owner of the project tree;
      * ``--cap-drop=ALL`` + ``--security-opt no-new-privileges`` — an unprivileged uid needs no capability
        to run node/git/claude (measured: ``CapEff: 0000000000000000`` and the turn is unaffected), and a
        setuid binary inside the image must not become a way back up;
      * a **tmpfs HOME** — nothing the CLI writes to ``~`` survives the turn, and the shared host
        ``~/.claude`` (user settings, hooks, every other project's transcripts) is simply not there;
      * this project's transcript dir, **READ-WRITE**, at its normal path — the one piece of ``~/.claude``
        a turn needs, so ``--resume`` keeps working;
      * the project bind, **READ-WRITE** — the one deliberate difference from the read-only Konzultácia
        sidecar: a build has to write. It is mounted at the SAME in-container path the backend drives claude
        with (``/opt/projects/<slug>``), so ``--resume``, ``cwd`` and every relative path resolve exactly as
        they did as a subprocess. The slug is validated and the resolved source containment-asserted BEFORE
        any mount is composed, so a ``..`` can never widen it;
      * on top of it, **READ-ONLY re-mounts of every file that decides what a later privileged turn
        executes** (:func:`frozen_project_paths`) — settings, agent deny profiles, skills, MCP config and
        the hook scripts those settings name;
      * ``~/.local`` read-only (the ``claude`` binary itself, which this image does not bundle);
      * ``-w`` the project — cwd = project, as in-process.

    ABSENT, and this is the point — asserted by the tests so a future addition turns them red: NO
    ``/var/run/docker.sock``, NO ``/opt/customers``, NO ``/opt/uat``, NO ``/opt/infra``, NO
    ``/home/icc/knowledge``, NO credentials store, NO shared ``~/.claude``. On the FILESYSTEM the sandbox
    sees its own project, its own transcript and its own binary — the network is a different question and
    :data:`NETWORK_RESIDUAL` answers it honestly.

    Raises:
        BuildSandboxUnavailable: unsafe slug, unmappable/escaping host path, or a malformed claude argv.
    """
    try:
        sandbox_paths.validate_project_slug(project_slug, sandbox=_SANDBOX_LABEL)
        # Import here rather than at module scope: ``claude_agent`` imports this module lazily for the same
        # reason (a cycle), and tests monkeypatch ``claude_agent.PROJECTS_ROOT``, which a module-level
        # ``from … import PROJECTS_ROOT`` would freeze at import time.
        from backend.services.claude_agent import PROJECTS_ROOT

        container_project_dir = str(PROJECTS_ROOT / project_slug)
        host_project_dir = sandbox_paths.container_to_host(
            container_project_dir, _CONTAINER_TO_HOST_PREFIX, sandbox=_SANDBOX_LABEL
        )
        sandbox_paths.assert_host_source_contained(host_project_dir, _CONTAINER_TO_HOST_PREFIX, sandbox=_SANDBOX_LABEL)
    except sandbox_paths.SandboxPathError as exc:
        raise BuildSandboxUnavailable(str(exc)) from exc

    if not claude_argv or not claude_argv[0]:
        raise BuildSandboxUnavailable(f"{_SANDBOX_LABEL}: unexpected claude argv (empty)")
    entrypoint_args = claude_argv[1:]

    session_dir = _host_session_dir(container_project_dir)
    container_session_dir = os.path.join(_SANDBOX_CLAUDE_DIR, "projects", session_dir_name(container_project_dir))

    argv = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--user",
        _SANDBOX_USER,
        # Nothing in a document-writing turn needs a capability, and no setuid binary may hand one back.
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
        # HOME is EPHEMERAL. The audited version bind-mounted the host's ~/.claude here, writable, which
        # gave a Návrh turn write access to the hooks a later ROOT turn executes and read access to every
        # other project's transcripts. tmpfs removes both without removing anything a turn uses.
        "--mount",
        f"type=tmpfs,destination={_SANDBOX_HOME},tmpfs-mode={_HOME_TMPFS_MODE}",
        # …and back into it, the ONE directory the turn genuinely needs from the config dir: its own
        # transcripts, so --resume of the warm (project, ai_agent) session still works. Created and chowned
        # to this uid by ``prepare_turn`` — the previous, root-owned state made --resume fail outright.
        "--mount",
        f"type=bind,source={session_dir},target={container_session_dir}",
        # The project — READ-WRITE (a build writes). ``--mount`` rather than ``-v`` on purpose: ``-v``
        # CREATES a missing source as an empty root-owned host dir, so a wrong path would mount an EMPTY
        # project and the agent would confidently build nothing. ``--mount`` refuses instead, and the
        # refusal is classified as unavailability — an honest failure rather than a silent lie.
        "--mount",
        f"type=bind,source={host_project_dir},target={container_project_dir}",
        # The shared ICC knowledge base — READ-ONLY (Director decision 23.08.2026). Charter §3(1) sends the
        # agent here for standards, decisions and past lessons; §3(3) would have it write back, and that
        # half stays refused by the kernel on purpose. Same in-container path as the backend uses, so a
        # path the agent read in an earlier phase still resolves.
        "--mount",
        f"type=bind,source={_KB_DIR},target={_KB_DIR},readonly",
    ]
    # …and the read-only re-deploy of everything in the project that governs a later, PRIVILEGED turn.
    # Kernel-refused both ways: writing gives EROFS, deleting-and-recreating gives EBUSY (both measured).
    for rel in frozen_project_paths(host_project_dir):
        frozen_source = os.path.join(host_project_dir, rel)
        frozen_target = os.path.join(container_project_dir, rel)
        argv += ["--mount", f"type=bind,source={frozen_source},target={frozen_target},readonly"]
    argv += [
        # The claude binary the image does not bundle — read-only: run it, never replace it.
        "--mount",
        f"type=bind,source={_CLAUDE_BIN_DIR},target={_CLAUDE_BIN_DIR},readonly",
        "-e",
        f"HOME={_SANDBOX_HOME}",
        "-e",
        f"CLAUDE_CONFIG_DIR={_SANDBOX_CLAUDE_DIR}",
    ]
    for name, value in _GIT_IDENTITY_ENV:
        argv += ["-e", f"{name}={value}"]
    for name in _PASSTHROUGH_ENV:
        # ``-e NAME`` with no ``=`` — docker copies the value from its own environment, so the value never
        # lands in the argv (and therefore never in ``ps``). An unset name is simply not passed.
        argv += ["-e", name]
    argv += [
        "-w",
        container_project_dir,
        "--entrypoint",
        "claude",
        sandbox_image(),
        *entrypoint_args,
    ]
    return argv


def log_network_residual_once() -> None:
    """Say ONCE per process what the sandbox does NOT isolate. Called from the first sandboxed dispatch.

    Modelled on :data:`consult_sandbox._EGRESS_RESTRICTION_FOLLOWUP`: a guarantee that is not implemented is
    stated out loud, not left to be discovered by whoever reads the argv closely enough.
    """
    global _network_residual_logged
    if not _network_residual_logged:
        logger.info(NETWORK_RESIDUAL)
        _network_residual_logged = True


async def reap_container(name: str) -> None:
    """Best-effort ``docker rm -f`` so a timed-out sandbox never leaks.

    ``--rm`` reaps a clean exit; after a timeout the docker-run CLIENT is killed while the container keeps
    running, so it has to be removed explicitly. Idempotent and never raises — cleanup must not mask the
    error that triggered it, nor hang the dispatch.
    """
    try:
        killer = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(killer.wait(), timeout=10)
    except (asyncio.TimeoutError, OSError):
        pass


# --------------------------------------------------------------------------------------------------------
# Readiness — enabled by default, so an unsatisfiable precondition must be visible BEFORE the first build
# --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SandboxStatus:
    """Whether a build sandbox could actually run right now, and if not, exactly which precondition is unmet.

    ``problems`` are operator-facing English strings naming the missing thing, never a vague "unhealthy".
    """

    enabled: bool
    ready: bool
    image: str
    problems: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            "image": self.image,
            "problems": list(self.problems),
            "phases": list(SANDBOXED_PHASES),
            # Published, not asserted: a boundary that is NOT in effect must be readable from the health
            # payload rather than inferred from the absence of an alarm.
            "network_residual": NETWORK_RESIDUAL,
        }


def preflight() -> SandboxStatus:
    """Probe the sandbox's hard preconditions (docker CLI, image, auth token, claude binary). Never raises.

    Cheap and synchronous; called once at startup. It is not a substitute for the per-turn failure — it
    exists so the operator learns the image is missing at boot rather than when the first Príprava turn
    stops, which is the difference between a five-minute fix and a stalled build.
    """
    enabled = sandbox_enabled()
    image = sandbox_image()
    if not enabled:
        return SandboxStatus(
            enabled=False,
            ready=False,
            image=image,
            problems=(
                "BUILD_SANDBOX is switched off — Príprava/Návrh run as backend subprocesses, with "
                "/opt/customers, /opt/uat, /opt/infra and the docker socket in reach",
            ),
        )
    problems: list[str] = []
    if shutil.which("docker") is None:
        return SandboxStatus(
            enabled=True,
            ready=False,
            image=image,
            problems=("docker CLI is not on PATH — the build sandbox cannot be launched at all",),
        )
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return SandboxStatus(
            enabled=True,
            ready=False,
            image=image,
            problems=(f"docker image inspect {image} could not be run ({exc}) — readiness is unknown",),
        )
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        problems.append(
            f"build sandbox image {image!r} is not available on this host "
            f"({detail[-1] if detail else 'no detail'}) — every Príprava/Návrh turn will FAIL. {_REMEDIATION}"
        )
    # The tmpfs HOME means the sandbox no longer inherits ``~/.claude/.credentials.json``: the subscription
    # token in the environment is now the ONLY way it authenticates. Unset = every turn fails at the CLI,
    # so it is a readiness problem, not a surprise for the first Príprava turn to discover.
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        problems.append(
            "CLAUDE_CODE_OAUTH_TOKEN is not set — the sandbox has an ephemeral HOME and no mounted "
            "~/.claude/.credentials.json, so the Claude MAX subscription token in the environment is the "
            "only authentication it has; every Príprava/Návrh turn would start unauthenticated"
        )
    if not os.path.isdir(_CLAUDE_BIN_DIR):
        problems.append(
            f"the claude binary dir {_CLAUDE_BIN_DIR} is missing — the sandbox would start without a usable claude"
        )
    if not os.path.isdir(_CLAUDE_HOME_DIR):
        problems.append(
            f"the Claude config dir {_CLAUDE_HOME_DIR} is missing — per-project session transcripts cannot "
            f"be created, so no turn could --resume its warm session"
        )
    return SandboxStatus(enabled=True, ready=not problems, image=image, problems=tuple(problems))


def log_startup_readiness() -> SandboxStatus:
    """Announce build-sandbox readiness once at boot; returns the status so a caller can act on it."""
    status = preflight()
    if status.ready:
        logger.info(
            "Build sandbox READY (image=%s) — %s turns run OS-isolated; every other phase still runs "
            "in-process with the host mounts (ICCINT-16 STEP 1 of 2). %s",
            status.image,
            "/".join(SANDBOXED_PHASES),
            NETWORK_RESIDUAL,
        )
    elif not status.enabled:
        logger.warning("Build sandbox OFF: %s", "; ".join(status.problems))
    else:
        logger.error(
            "Build sandbox ENABLED but NOT READY — every Príprava/Návrh turn will fail until this is "
            "fixed. Problems: %s",
            "; ".join(status.problems),
        )
    return status

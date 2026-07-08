"""OS-level read-only sidecar for the Konzultácia turn (konzultacia-sidecar-sandbox.md, Fix 2).

The read-only consult guarantee must be enforced by the KERNEL, not by a CLI deny-list (a deny-list
can't be completed — a live smoke found Task/Workflow/Skill/ToolSearch… kept leaking; per the
Bash-permission lesson only OS isolation is robust). This module runs the CONSULT turn inside an
ephemeral ``docker run --rm`` sibling of THIS backend image (launched via the mounted
``/var/run/docker.sock``, same as any sibling launch) where:

  * the project is bind-mounted ``:ro`` → a raw-shell write is kernel-refused ("Read-only file system");
    THIS is the guarantee — the AI can read the one project but cannot mutate it (no write tools either);
  * ONLY the project (``:ro``) + the ``~/.claude`` auth/config dir (WRITABLE — so ``claude`` persists and
    ``--resume``s its OWN session state, exactly as the in-container build turns do; a writable config dir
    does NOT let the AI touch the project) are mounted — NO docker.sock, NO ``/opt/customers``, NO
    ``/opt/uat``, NO credentials store, NO ``/opt/infra``, NO knowledge mount → the sidecar can see and
    reach nothing but the one project it is consulting;
  * the per-turn ``claude`` flags are byte-identical to the in-process turn (reused from
    :func:`claude_agent.build_claude_argv`) so the sidecar and in-process turns differ only in transport.

AUTH is the Claude MAX 20× SUBSCRIPTION (OAuth token in ``~/.claude/.credentials.json``), NOT the
Anthropic developer API (ICC rule §15 — never the direct Anthropic API). The mounted ``~/.claude``
carries exactly that OAuth token, so the sidecar authenticates via the MAX subscription just as the
backend does today; no API key is involved and no credential is ever printed.

Network hardening (egress restriction to only the MAX-subscription endpoint) is a documented FOLLOW-UP:
this pass ships on the default bridge + the deny-by-default read-only permission-mode (WebFetch/WebSearch
are NOT in the allow-set → denied). :data:`_EGRESS_RESTRICTION_FOLLOWUP` records that honestly (do NOT
claim egress restriction that is not implemented). See :func:`run_consult_in_sandbox`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from backend.config.settings import settings
from backend.services import claude_agent
from backend.services.claude_agent import (
    ClaudeAgentError,
    ClaudeAgentTimeout,
    UsageMetadata,
    _kill_process_tree,
    _load_charter,
    _structured_from,
    _usage_from,
)

#: The ICC-canonical kebab-case project-slug rule, reused verbatim (DRY) so the sidecar accepts EXACTLY the
#: slugs the project system considers valid (identical rule in ``project_specs`` and ``agent_terminal``).
#: Rejects ``..`` / ``/`` / empty / anything non-slug BEFORE it is composed into a ``-v`` bind source (Fix 1).
from backend.services.project_specs import _SLUG_RE as _PROJECT_SLUG_RE

logger = logging.getLogger(__name__)

#: In-container auth/config dir (mounted WRITABLE) + the env that points ``claude`` at it. ``CLAUDE_CONFIG_DIR``
#: points at this mount — claude READS its MAX-subscription OAuth token there AND WRITES/``--resume``s its own
#: session state there. A live consult runs ``claude --resume <build-session-uuid>`` (it resumes the done
#: version's existing session), which writes session state under ``CLAUDE_CONFIG_DIR``; a ``:ro`` mount made
#: the kernel refuse that write (EROFS) → the turn failed (live bug, v3 2026-07-08). The mount is therefore
#: writable, exactly as the in-container build turns already write ``~/.claude`` today. This does NOT weaken
#: the project read-only guarantee: the PROJECT stays ``:ro`` (kernel-enforced) and the AI has no write tools.
_CLAUDE_AUTH_DIR = "/home/andros/.claude"

#: The sidecar runs as this unprivileged host user (the same user the backend runs as) — never root.
_SIDECAR_USER = "andros"

#: In-container project dir → HOST path for the sidecar ``-v`` bind. A sibling ``docker run`` is resolved
#: by the daemon on the HOST, so the bind SOURCE must be the host path, not the backend's in-container view
#: (konzultacia-sidecar-sandbox.md §Proven feasibility). Regular projects live under ``/opt/projects-v3`` on
#: the host but are mounted at ``/opt/projects`` in the backend; customer projects are the same path on both.
_CONTAINER_TO_HOST_PREFIX: tuple[tuple[str, str], ...] = (
    ("/opt/projects", "/opt/projects-v3"),
    ("/opt/customers", "/opt/customers"),
)

#: stderr signatures that mean the SIDECAR itself could not run (docker CLI/daemon/image problem) — as
#: opposed to ``claude`` running and failing. On a match we raise :class:`SidecarUnavailable` so the caller
#: degrades to the in-process read-only turn (still tool-profile read-only) with an honest log, instead of
#: surfacing a hard consult failure. A ``claude`` failure inside a healthy sidecar stays a
#: :class:`ClaudeAgentError` (retried/handled exactly like the in-process turn).
_SIDECAR_UNAVAILABLE_RE = re.compile(
    r"(cannot connect to the docker daemon"
    r"|is the docker daemon running"
    r"|permission denied while trying to connect to the docker daemon"
    r"|/var/run/docker\.sock"
    r"|unable to find image"
    r"|no such image"
    r"|pull access denied)",
    re.IGNORECASE,
)

#: Honest record that network-egress-restriction is NOT implemented in this pass (spec: "do NOT silently
#: claim it"). Logged once per process when the first sidecar launches on the default bridge.
_EGRESS_RESTRICTION_FOLLOWUP = (
    "consult sidecar runs on the DEFAULT docker bridge — network-egress-restriction to only the "
    "MAX-subscription endpoint is a documented FOLLOW-UP, not yet implemented "
    "(konzultacia-sidecar-sandbox.md Part 1 §Network). The read-only guarantee still holds via the "
    "deny-by-default permission-mode (WebFetch/WebSearch denied) + the KERNEL :ro project mount."
)
_egress_followup_logged = False


class SidecarUnavailable(ClaudeAgentError):
    """The consult sidecar could not be launched (docker CLI missing, daemon unreachable, image absent).

    A SUBCLASS of :class:`ClaudeAgentError` so a stray propagation degrades to a normal claude error rather
    than an uncaught exception, but callers (:func:`claude_agent._invoke_once`) catch it explicitly to
    DEGRADE to the in-process read-only turn with an honest WARNING — distinct from a ``claude`` failure
    INSIDE a healthy sidecar (a plain :class:`ClaudeAgentError`, handled/retried like the in-process turn)."""


def sandbox_enabled() -> bool:
    """Whether CONSULT turns route through the OS-isolated sidecar (konzultacia-sidecar-sandbox.md Part 2).

    Default ON ("default on in prod"); set ``CONSULT_SANDBOX`` to ``0``/``false``/``no``/``off`` to force
    the in-process read-only fallback (still tool-profile read-only, just not kernel-isolated). Read at turn
    time (env, not a cached setting) so the operational kill-switch flips without a process restart."""
    return os.environ.get("CONSULT_SANDBOX", "1").strip().lower() not in ("0", "false", "no", "off", "")


def _host_project_path(container_project_dir: str) -> str:
    """Translate the backend's in-container project dir → the HOST path for the ``-v`` bind source."""
    for container_prefix, host_prefix in _CONTAINER_TO_HOST_PREFIX:
        if container_project_dir == container_prefix or container_project_dir.startswith(container_prefix + "/"):
            return host_prefix + container_project_dir[len(container_prefix) :]
    raise SidecarUnavailable(
        f"consult sidecar: cannot map in-container project path {container_project_dir!r} to a host path"
    )


def _validate_project_slug(project_slug: str) -> None:
    """Reject any non-canonical project slug BEFORE it is composed into a ``-v`` bind source (Fix 1).

    ``pathlib`` does NOT normalize ``..``, so an unvalidated slug of ``..`` would compose the bind SOURCE
    ``/opt/projects-v3/..`` → docker would mount ALL of ``/opt`` (every customer / uat / infra / project)
    ``:ro`` into the sidecar, defeating the NEGATIVE half of the read-only guarantee (a cross-tenant leak,
    even though nothing is writable). Reuses the ICC-canonical :data:`_PROJECT_SLUG_RE` (DRY); a bad slug
    raises :class:`SidecarUnavailable` so the caller degrades to the in-process read-only turn."""
    if not _PROJECT_SLUG_RE.match(project_slug):
        raise SidecarUnavailable(f"consult sidecar: refusing unsafe project slug {project_slug!r}")


def _assert_host_source_contained(host_project_dir: str) -> None:
    """Belt-and-suspenders: the RESOLVED (symlink-followed) bind SOURCE must stay strictly UNDER one of the
    intended host project prefixes (Fix 1). Even if a future prefix change or a symlink composed a source
    that escaped ``/opt/projects-v3/<slug>`` or ``/opt/customers/<slug>``, refuse it here rather than
    silently broadening the ``:ro`` mount. Independent of :func:`_validate_project_slug` on purpose — two
    orthogonal layers guarding the same invariant."""
    real = os.path.realpath(host_project_dir)
    for _container_prefix, host_prefix in _CONTAINER_TO_HOST_PREFIX:
        if real.startswith(os.path.realpath(host_prefix) + os.sep):
            return
    raise SidecarUnavailable(
        f"consult sidecar: refusing bind source {host_project_dir!r} — resolves outside the project roots"
    )


def build_sidecar_argv(
    *,
    project_slug: str,
    container_name: str,
    claude_argv: list[str],
) -> list[str]:
    """Compose the EXACT ``docker run`` argv for the consult sidecar (the mounts ARE the guarantee).

    ``claude_argv`` is the full ``["claude", "-p", …]`` from :func:`claude_agent.build_claude_argv`; its
    leading ``"claude"`` is dropped here because ``--entrypoint claude`` provides it, and the rest is
    appended AFTER the image. Every ``docker run`` option below is mandatory:

      * ``--rm`` ephemeral + ``--name`` (so a hung container can be ``docker kill``/reaped — never leaked);
      * ``--user andros`` (unprivileged, never root);
      * project bind ``:ro`` (the KERNEL read-only guarantee) at the SAME in-container path the backend uses
        (``/opt/projects/<slug>``), sourced from the translated HOST path — the slug is validated and the
        resolved source is containment-asserted FIRST so a ``..`` can never broaden the mount (Fix 1);
      * ``~/.claude`` bind WRITABLE (MAX-subscription OAuth) + ``CLAUDE_CONFIG_DIR`` at that mount, so claude
        reads its token AND persists/``--resume``s its own session state there (as the build turns do) — the
        writable config dir does NOT let the AI touch the project (no write tools + the kernel ``:ro`` project);
      * ``-w`` the project dir (cwd = project, as in-process);
      * ``--entrypoint claude`` + the reused per-turn claude flags.

    Deliberately ABSENT (the negative half of the guarantee — asserted by the tests): NO
    ``/var/run/docker.sock``, NO ``/opt/customers``, NO ``/opt/uat``, NO credentials store, NO
    ``/opt/infra``, NO knowledge mount, NO extra network. The sidecar sees ONLY the one project + auth.
    """
    # Fix 1 — validate the slug and containment-assert the resolved source BEFORE composing any ``-v``.
    _validate_project_slug(project_slug)
    container_project_dir = str(claude_agent.PROJECTS_ROOT / project_slug)
    host_project_dir = _host_project_path(container_project_dir)
    _assert_host_source_contained(host_project_dir)
    image = settings.consult_sandbox_image
    if not claude_argv or claude_argv[0] != "claude":
        raise SidecarUnavailable("consult sidecar: unexpected claude argv (missing 'claude' head)")
    entrypoint_args = claude_argv[1:]
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--user",
        _SIDECAR_USER,
        # project → KERNEL read-only (the hard guarantee). Same in-container path the backend drives claude
        # with, so --resume/cwd/relative reads all resolve identically to the in-process turn.
        "-v",
        f"{host_project_dir}:{container_project_dir}:ro",
        # MAX-subscription auth/config dir mounted WRITABLE (OAuth token in). A live consult runs
        # `claude --resume`, which WRITES session state under CLAUDE_CONFIG_DIR → a :ro mount kernel-refused
        # it (EROFS, live bug v3 2026-07-08). Writable so claude persists/resumes its own session, exactly as
        # the in-container build turns do — the project stays :ro (the guarantee) and the AI has no write tools.
        "-v",
        f"{_CLAUDE_AUTH_DIR}:{_CLAUDE_AUTH_DIR}",
        "-e",
        f"CLAUDE_CONFIG_DIR={_CLAUDE_AUTH_DIR}",
        "-w",
        container_project_dir,
        "--entrypoint",
        "claude",
        image,
        *entrypoint_args,
    ]


async def _reap_container(container_name: str) -> None:
    """Best-effort ``docker rm -f`` so a hung/killed sidecar never leaks (``--rm`` covers a clean exit;
    a ``docker kill``+reap is needed after a timeout where the client was killed but the container lives).

    Idempotent: a "no such container" (already reaped by ``--rm``) is swallowed. Never raises — cleanup
    must not mask the original error, nor hang the dispatch."""
    try:
        killer = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(killer.wait(), timeout=10)
    except (asyncio.TimeoutError, OSError):
        pass  # docker missing / reap timed out — the --rm still reaps on the daemon side; never hang here


async def run_consult_in_sandbox(
    *,
    project_slug: str,
    claude_session_id: UUID,
    prompt: str,
    charter_path: Optional[Path] = None,
    timeout: int = claude_agent.CLAUDE_INVOKE_TIMEOUT,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    json_schema: Optional[dict] = None,
    allowed_tools: Optional[list[str]] = None,
) -> tuple[str, Optional[UsageMetadata], Optional[dict]]:
    """Run ONE read-only consult turn inside an isolated sidecar; return ``(text, usage, structured_output)``.

    Transport-agnostic mirror of :func:`claude_agent._invoke_once`'s json path: it composes the SAME
    per-turn ``claude`` flags (:func:`claude_agent.build_claude_argv`, always ``--output-format json`` —
    the sidecar is non-streaming), wraps them in the sidecar ``docker run`` (:func:`build_sidecar_argv`),
    runs it with the SAME timeout + process-tree kill parity, and parses the SAME json envelope with the
    EXISTING :func:`claude_agent._usage_from` / :func:`_structured_from` so the caller is unchanged.

    Raises:
        SidecarUnavailable: the sidecar could NOT run (docker CLI missing / daemon unreachable / image
            absent) → the caller degrades to the in-process read-only turn with an honest log.
        ClaudeAgentTimeout: the sidecar exceeded ``timeout`` (container ``docker kill``ed + reaped).
        ClaudeAgentError: ``claude`` ran inside a healthy sidecar and failed (non-zero exit, decode/JSON
            failure, or a json envelope with no ``result``) — handled/retried like the in-process turn.
    """
    global _egress_followup_logged

    # Reuse the SINGLE per-turn flag source so the sidecar and in-process turns stay byte-identical except
    # for transport. First turn loads the charter (descriptive error preserved) → --session-id; else --resume.
    charter_text = _load_charter(charter_path) if charter_path is not None else None
    claude_argv = claude_agent.build_claude_argv(
        streaming=False,  # sidecar is always non-streaming json (Part 1) — the envelope carries usage/result
        claude_session_id=claude_session_id,
        prompt=prompt,
        charter_text=charter_text,
        model=model,
        effort=effort,
        json_schema=json_schema,
        allowed_tools=allowed_tools,
    )

    container_name = f"nex-consult-{uuid4().hex[:16]}"
    docker_argv = build_sidecar_argv(
        project_slug=project_slug,
        container_name=container_name,
        claude_argv=claude_argv,
    )

    if not _egress_followup_logged:
        logger.info(_EGRESS_RESTRICTION_FOLLOWUP)
        _egress_followup_logged = True
    logger.info(
        "Launching consult sidecar: project=%s session=%s container=%s timeout=%ds",
        project_slug,
        claude_session_id,
        container_name,
        timeout,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *docker_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # A single json envelope (with grammar-constrained structured_output) can far exceed the 64 KB
            # default StreamReader limit — mirror the in-process generous bound (CR-NS-018).
            limit=claude_agent._STREAM_LINE_LIMIT,
            # Own session/process-group leader so a timeout SIGKILLs the whole docker-run client tree.
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        # No ``docker`` CLI in this environment → the sidecar cannot run at all. Degrade honestly.
        raise SidecarUnavailable(f"consult sidecar: docker CLI unavailable ({exc})") from exc

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        await _kill_process_tree(proc)  # kill the docker-run client tree...
        await _reap_container(container_name)  # ...AND docker kill/reap the container it left running
        raise ClaudeAgentTimeout(f"consult sidecar timed out after {timeout}s") from exc
    except asyncio.CancelledError:
        # Caller task cancelled mid-turn — never leak the container.
        await _kill_process_tree(proc)
        await _reap_container(container_name)
        raise
    except Exception:
        # ANY OTHER unexpected error mid-run (e.g. an OSError in communicate, a decode failure) must NOT
        # leak the running container — ``--rm`` only reaps a CLEAN exit (Fix 3). Timeout/Cancelled are
        # handled above; a clean exit never enters here, so there is no double-reap.
        await _kill_process_tree(proc)
        await _reap_container(container_name)
        raise

    if proc.returncode != 0:
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if _SIDECAR_UNAVAILABLE_RE.search(stderr_text):
            # docker/daemon/image problem — the sidecar never ran claude. Degrade to in-process.
            raise SidecarUnavailable(f"consult sidecar could not start: {stderr_text[:500]}")
        # claude ran inside a healthy sidecar and exited non-zero — same contract as the in-process turn
        # (transient signatures like 529 ride the message through claude_agent.invoke_claude's retry).
        raise ClaudeAgentError(f"consult sidecar claude exited with code {proc.returncode}: {stderr_text[:500]}")

    raw = stdout.decode("utf-8", errors="replace").strip()
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClaudeAgentError(f"consult sidecar json output not parseable: {exc}") from exc
    if not isinstance(envelope, dict) or "result" not in envelope:
        raise ClaudeAgentError("consult sidecar json output has no 'result' field")
    return str(envelope["result"]).strip(), _usage_from(envelope), _structured_from(envelope)

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
import shutil
import subprocess
import time
from dataclasses import dataclass
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
    r"|pull access denied"
    # ``--mount type=bind`` refusing an absent host source (see :func:`build_sidecar_argv`). The sidecar
    # never ran claude, so it belongs here — a COUNTED degrade, not a hard consult failure.
    r"|bind source path does not exist"
    r"|invalid mount config)",
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


# --------------------------------------------------------------------------------------------------------
# Readiness — the sidecar is enabled by default, so an UNSATISFIABLE precondition must be LOUD, not silent
# --------------------------------------------------------------------------------------------------------
#
# Audited failure: ``consult_sandbox_image`` defaulted to a tag that does not exist on this host, so EVERY
# consult raised :class:`SidecarUnavailable` ("unable to find image") and degraded to the in-process turn.
# The degrade path logged one WARNING per turn and nothing else — no operator surface said so — so the
# kernel-enforced read-only guarantee the design promises was not in effect on ANY consult, while every
# health surface read fine. Readiness is therefore PROBED and PUBLISHED:
#
#   * :func:`preflight` answers "could a sidecar run right now, and if not, exactly why" — surfaced by
#     ``GET /health`` (``consult_sandbox``) and logged at ERROR on startup when enabled-but-not-ready;
#   * :func:`record_degradation` counts the turns that actually fell back, so the health payload shows the
#     guarantee lapsing in real time rather than only the static config being wrong.
#
# Building the missing image is a Director decision, NOT something this module papers over: an unready
# sandbox still degrades (a consult must not become unusable), it simply can no longer do so quietly.

#: How long a successful/failed docker+image probe is reused before re-probing. ``GET /health`` is polled by
#: the container HEALTHCHECK every 30s and a ``docker image inspect`` is a daemon round-trip, so the answer
#: is cached — short enough that a freshly built image shows up on the next poll.
_PREFLIGHT_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class SandboxStatus:
    """Whether the OS-isolated consult sidecar can actually run, and what has happened to it so far.

    ``ready`` is the honest verdict: ``enabled and not problems``. ``problems`` are operator-facing English
    strings naming the exact missing precondition (same register as this module's logs), never a vague
    "unhealthy". ``degraded_turns``/``last_degradation`` accumulate for the process lifetime so a lapsed
    guarantee is visible even when the static config later gets fixed."""

    enabled: bool
    ready: bool
    image: str
    problems: tuple[str, ...] = ()
    degraded_turns: int = 0
    last_degradation: Optional[str] = None

    def as_dict(self) -> dict:
        """JSON-safe payload for ``GET /health``.

        ``host_bind_sources`` publishes the container→HOST path translation the sidecar will bind
        (:data:`_CONTAINER_TO_HOST_PREFIX`). It is reported, never asserted: the backend container cannot
        stat a host path it does not itself mount, and claiming a verification we did not perform is exactly
        the dishonesty this whole payload exists to end. An operator reading a host prefix that does not
        exist on the host has found the problem."""
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            "image": self.image,
            "problems": list(self.problems),
            "degraded_turns": self.degraded_turns,
            "last_degradation": self.last_degradation,
            "host_bind_sources": {container: host for container, host in _CONTAINER_TO_HOST_PREFIX},
        }


@dataclass
class _DegradationLedger:
    """Process-lifetime tally of consult turns that ran WITHOUT the kernel guarantee."""

    count: int = 0
    last_reason: Optional[str] = None


_ledger = _DegradationLedger()

#: ``(monotonic_deadline, problems)`` — the cached result of the docker/image probe. ``None`` = never probed.
_preflight_cache: Optional[tuple[float, tuple[str, ...]]] = None

#: Signatures that mean the DAEMON is unreachable rather than the image being absent. Deliberately narrower
#: than :data:`_SIDECAR_UNAVAILABLE_RE` — "Error response from daemon: No such image" also contains "daemon",
#: so the two causes must not be told apart by that word.
_DAEMON_UNREACHABLE_RE = re.compile(
    r"(cannot connect to the docker daemon|is the docker daemon running|permission denied while trying to connect)",
    re.IGNORECASE,
)


def _auth_dir_present() -> bool:
    """Whether the MAX-subscription auth/config dir the sidecar binds exists. Its own function so a test can
    stub THIS rather than ``Path.is_dir`` globally (which would silently answer for every other caller)."""
    try:
        return Path(_CLAUDE_AUTH_DIR).is_dir()
    except OSError:
        return False


def _probe_problems() -> tuple[str, ...]:
    """Probe the sidecar's hard preconditions. Pure I/O, no caching — :func:`preflight` owns the cache."""
    problems: list[str] = []
    if shutil.which("docker") is None:
        # Without the CLI nothing else is checkable — the daemon and image probes both go through it.
        return ("docker CLI is not on PATH — the sidecar cannot be launched at all",)
    image = settings.consult_sandbox_image
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True,
            text=True,
            # 3s, not 10. `/health` is the container's OWN healthcheck (compose: timeout 5s, retries 3),
            # and this probe is a Docker-daemon round-trip. A 10s worst case exceeds the probe budget, so a
            # merely SLOW daemon (a heavy image build, socket contention) would mark a perfectly healthy
            # backend unhealthy — and the CI deploy gate, now un-muted, would fail the deploy on that
            # verdict. A timeout here reports "sandbox unverifiable", which is the honest answer and does
            # not take the whole container down with it.
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (f"docker image inspect {image} could not be run ({exc}) — sidecar readiness is unknown",)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip().splitlines()
        detail = stderr[-1] if stderr else "no detail"
        if _DAEMON_UNREACHABLE_RE.search(detail):
            problems.append(f"docker daemon is unreachable ({detail}) — the sidecar cannot be launched")
        else:
            problems.append(
                f"consult sandbox image {image!r} does not exist on this host ({detail}) — every consult "
                f"falls back to the in-process turn, so the project is NOT kernel-enforced read-only. "
                f"Build/pull the image or point CONSULT_SANDBOX_IMAGE at an existing tag."
            )
    if not _auth_dir_present():
        problems.append(
            f"the Claude auth/config dir {_CLAUDE_AUTH_DIR} is missing — the sidecar would start "
            f"unauthenticated (no MAX-subscription OAuth token to read)"
        )
    return tuple(problems)


def preflight(*, refresh: bool = False) -> SandboxStatus:
    """Report whether a consult sidecar could run RIGHT NOW, naming every unmet precondition.

    Cached for :data:`_PREFLIGHT_TTL_SECONDS` (the probe is a daemon round-trip and ``/health`` is polled);
    pass ``refresh=True`` to force a re-probe. Never raises — an unprobeable environment is reported as a
    problem, never swallowed into a healthy-looking ``ready``."""
    global _preflight_cache

    enabled = sandbox_enabled()
    if not enabled:
        # The kill-switch is a DELIBERATE operator choice, not a fault — report it as such (ready=False,
        # because the kernel guarantee is genuinely not in effect) with no probe and no alarming problem.
        return SandboxStatus(
            enabled=False,
            ready=False,
            image=settings.consult_sandbox_image,
            problems=("CONSULT_SANDBOX is switched off — consults run in-process (tool-profile read-only only)",),
            degraded_turns=_ledger.count,
            last_degradation=_ledger.last_reason,
        )
    now = time.monotonic()
    if refresh or _preflight_cache is None or _preflight_cache[0] <= now:
        _preflight_cache = (now + _PREFLIGHT_TTL_SECONDS, _probe_problems())
    problems = _preflight_cache[1]
    return SandboxStatus(
        enabled=True,
        ready=not problems,
        image=settings.consult_sandbox_image,
        problems=problems,
        degraded_turns=_ledger.count,
        last_degradation=_ledger.last_reason,
    )


def record_degradation(reason: str) -> None:
    """Count a consult turn that ran WITHOUT the kernel-enforced read-only guarantee, and say so loudly.

    Called from the single degrade seam in :func:`claude_agent._invoke_once`. ERROR, not WARNING: a promised
    security boundary that is not in effect is not routine noise. The tally rides on ``GET /health`` so the
    lapse is visible without grepping logs."""
    _ledger.count += 1
    _ledger.last_reason = reason
    logger.error(
        "CONSULT SANDBOX DEGRADED (%d turn(s) this process): %s — the consult ran in-process, so the project "
        "was tool-profile read-only but NOT kernel-enforced read-only (konzultacia-sidecar-sandbox.md). "
        "Preflight: %s",
        _ledger.count,
        reason,
        "; ".join(preflight().problems) or "no static problem found — a transient daemon failure?",
    )


def log_startup_readiness() -> SandboxStatus:
    """Announce sidecar readiness once at boot; returns the status so the caller can act on it.

    An enabled-but-unready sandbox is logged at ERROR with the exact remediation. The audited deployment had
    no such line anywhere: it booted, reported healthy, and every consult quietly lost the guarantee."""
    status = preflight(refresh=True)
    if status.ready:
        logger.info("Consult sidecar READY (image=%s) — consults are kernel-enforced read-only", status.image)
    elif not status.enabled:
        logger.warning("Consult sidecar OFF: %s", "; ".join(status.problems))
    else:
        logger.error(
            "Consult sidecar ENABLED but NOT READY — every Konzultácia will silently lose its kernel "
            "read-only guarantee and fall back in-process. Problems: %s",
            "; ".join(status.problems),
        )
    return status


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
        #
        # ``--mount`` rather than ``-v`` ON PURPOSE (and the reason both binds below moved): ``-v`` CREATES a
        # missing bind source as an empty root-owned host dir, so a wrong/absent host path mounts an EMPTY
        # project :ro and the consult answers confidently about nothing — a wrong answer with no error
        # anywhere. ``--mount`` refuses ("bind source path does not exist"), which
        # :data:`_SIDECAR_UNAVAILABLE_RE` classifies as unavailable → an honest, COUNTED degrade
        # (:func:`record_degradation`) instead of a silent lie.
        "--mount",
        f"type=bind,source={host_project_dir},target={container_project_dir},readonly",
        # MAX-subscription auth/config dir mounted WRITABLE (OAuth token in). A live consult runs
        # `claude --resume`, which WRITES session state under CLAUDE_CONFIG_DIR → a :ro mount kernel-refused
        # it (EROFS, live bug v3 2026-07-08). Writable so claude persists/resumes its own session, exactly as
        # the in-container build turns do — the project stays :ro (the guarantee) and the AI has no write tools.
        "--mount",
        f"type=bind,source={_CLAUDE_AUTH_DIR},target={_CLAUDE_AUTH_DIR}",
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

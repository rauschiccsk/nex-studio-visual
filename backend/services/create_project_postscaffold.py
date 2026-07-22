"""F-004 Stage 5+6: K-004 smoke test + K-005 CI/CD wire-up + branch protection.

Both stages sú best-effort — partial success acceptable. Failure logged ako
warning, NIE 500. Manažér môže re-run / wire manually ak treba.

Per F-004 spec §3.4 + §3.5 + spec O-3 (branch protection opt-in).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Container-correct templates dir: parents[2] of this file is the repo root — ``/app`` inside the backend
# image (Dockerfile ``COPY templates/ ./templates/``), ``/opt/projects/nex-studio`` in dev. The previous
# hardcoded host path ``/opt/projects/nex-studio/templates`` does NOT exist inside the container (where
# ``/opt/projects`` is the bind-mounted PROJECT workspace), so every template seed below silently logged
# "template missing" and skipped. Mirrors ``uat_provisioner.NEX_STUDIO_ROOT``.
NEX_STUDIO_ROOT = Path(__file__).resolve().parents[2]
NEX_STUDIO_TEMPLATES = NEX_STUDIO_ROOT / "templates"
CICD_TEMPLATE = NEX_STUDIO_TEMPLATES / "github-actions-workflow.yml"
#: The ruff / type-check pre-commit hook (blocks a commit that CI Lint would reject) — copied into
#: each new project's ``.githooks/`` and activated via ``core.hooksPath`` at scaffold time (v4.0.29).
PRECOMMIT_HOOK_TEMPLATE = NEX_STUDIO_TEMPLATES / "pre-commit-hook.sh"
# gate-g-hardening GAP 1 (CR-B): the behavioural release-acceptance script the engine runs at gate_g.
RELEASE_SMOKE_TEMPLATE = NEX_STUDIO_TEMPLATES / "release_smoke_test.sh"
# CR-R2-3 (#3): the CI `migrate` job's dotenv renderer — shipped alongside ci.yml so the gate can run.
CI_RENDER_HELPER_TEMPLATE = NEX_STUDIO_TEMPLATES / "ci_render_dotenv.py"
SMOKE_BUILD_TIMEOUT = 300  # 5 min — minimal smoke is docker compose build only
SMOKE_FULL_TIMEOUT = 600  # 10 min — full smoke incl up + health
CICD_TIMEOUT = 60
BRANCH_PROTECTION_TIMEOUT = 30

# Containerized self-hosted CI runner (Director 2026-07-16). The backend runs INSIDE a container and cannot
# install a HOST systemd runner (no systemctl/sudo/host runner dir) like the 13 legacy ones — so it runs the
# runner as a Docker container via the mounted docker.sock. See :func:`_provision_ci_runner` for the full why.
# Image tag pinned to the SAME runner version as the host systemd runners (2.335.1).
CI_RUNNER_IMAGE = "myoung34/github-runner:2.335.1"
CI_RUNNER_PROVISION_TIMEOUT = 300  # a cold `docker run` may pull the runner image first
CI_RUNNER_TEARDOWN_TIMEOUT = 60


# --- CR-V2-005 archetype surface composition --------------------------------
# A project archetype is a preset SURFACE COMPOSITION (design §4.2): a project
# is one backend + one-or-more frontend surfaces. ``standard`` and ``web`` are
# the only archetypes shipped in v2.0.0 (Mobil is a deferred future round,
# §8 Open #1). The per-archetype surface plan below is the single source of
# truth the scaffold composes:
#
#   * ``standard`` → backend + ONE app-frontend surface (today's shape).
#   * ``web``      → backend + an admin-frontend surface + a public-site
#     surface (a managed/monitored site whose admin-FE configures the site and
#     shows its metrics — the SECOND frontend surface). nex-shared supplies the
#     cross-surface web-platform solutions.
#
# Both archetypes additionally pick a login flavour from ``auth_mode``
# (``password`` = username+password login like NEX Studio / ``token`` =
# token-launch like NEX Inbox) wired onto the backend + each frontend surface.
_ARCHETYPE_SURFACES: dict[str, tuple[str, ...]] = {
    "standard": ("app-frontend",),
    "web": ("admin-frontend", "public-site"),
}


# --- CR-V2-018 v2 two-agent charter provisioning ----------------------------
class ProvisioningError(RuntimeError):
    """v2 agent-charter provisioning failed. Unlike the best-effort post-scaffold seeds this is a HARD
    failure the caller surfaces (500 + rollback): the engine fail-closes on a missing ai-agent/auditor
    charter (``claude_agent._load_charter``), so a silent skip would re-introduce the "Agent dispatch
    failed — pipeline blocked" bug at first dispatch."""


#: charter-path slug → (role charter template, role settings template) under ``templates/``.
_V2_AGENTS: dict[str, tuple[str, str]] = {
    "ai-agent": ("ai-agent-charter.md", "ai-agent-settings.json"),
    "auditor": ("auditor-charter.md", "auditor-settings.json"),
}
_AGENT_SHARED_BASE = "agent-shared-base.md"
_UNIVERSAL_CLAUDE_MD = "project-claude-md.md"
_CHARTER_SEP = "\n\n---\n\n"
#: v1-era charter dirs the v1 ``init.sh`` still emits but the v2 two-agent engine never reads (it reads
#: ONLY ``.claude/agents/{ai-agent,auditor}/CLAUDE.md``) — removed so a v2 project is clean v2 shape.
_V1_AGENT_DIRS = ("designer", "implementer", "customer")


def provision_v2_agent_charters(project_root: Path, slug: str, project_name: str) -> None:
    """Write the v2 two-agent ``Pravidlá agenta`` charters into the freshly-scaffolded project and
    normalise it to v2 shape. HARD requirement (raises :class:`ProvisioningError` on failure).

    ``project_root`` MUST be the path the engine treats as the project root — ``PROJECTS_ROOT / slug``
    (= the agent's cwd + the charter-read root at dispatch, ``claude_agent``). Callers pass that, NOT
    ``project.source_path``: the two are equal under the system invariant, but the engine hardcodes
    ``PROJECTS_ROOT / slug``, so binding here guarantees the charter files land where the engine reads
    them AND the absolute deny/allow globs in ``settings.json`` match the agent's actual cwd.

    For each v2 role (``ai-agent``, ``auditor``): write ``.claude/agents/<role>/CLAUDE.md`` =
    shared base + role charter (concatenated — the engine reads the single file via
    ``--append-system-prompt``, so the join MUST happen here at scaffold time), and ``settings.json``
    with the ``<PROJECT_ROOT>`` placeholder substituted to ``project_root``. Then replace the v1
    universal ``CLAUDE.md`` (which the ``claude`` CLI auto-loads from cwd) with the v2-native one and
    remove the v1-only charter dirs (designer/implementer/customer) the engine never reads.

    No-op (info log, no raise) when there is no checkout on disk (dry-run / disabled bootstrap),
    mirroring ``project_memory.seed_memory`` and Stage 4."""
    claude_dir = project_root / ".claude"
    if not project_root.is_dir() or not claude_dir.is_dir():
        logger.info(
            "v2 charter provisioning SKIPPED — no scaffold on disk for slug=%s (dry-run / disabled bootstrap)",
            slug,
        )
        return

    base_tpl = NEX_STUDIO_TEMPLATES / _AGENT_SHARED_BASE
    if not base_tpl.is_file():
        raise ProvisioningError(
            f"v2 charter provisioning failed (slug={slug}): shared base template missing at {base_tpl}"
        )
    base_text = base_tpl.read_text(encoding="utf-8").rstrip()
    # <PROJECT_ROOT> = the project root = the agent's cwd at dispatch (claude_agent runs with
    # cwd=PROJECTS_ROOT/slug), so the absolute deny/allow globs match the files the agent touches.
    project_root_str = str(project_root)

    for role_slug, (charter_tpl_name, settings_tpl_name) in _V2_AGENTS.items():
        charter_tpl = NEX_STUDIO_TEMPLATES / charter_tpl_name
        settings_tpl = NEX_STUDIO_TEMPLATES / settings_tpl_name
        if not charter_tpl.is_file() or not settings_tpl.is_file():
            raise ProvisioningError(
                f"v2 charter provisioning failed (slug={slug}): missing template(s) for role "
                f"{role_slug} (expected {charter_tpl} + {settings_tpl})"
            )
        role_dir = claude_dir / "agents" / role_slug
        try:
            role_dir.mkdir(parents=True, exist_ok=True)
            (role_dir / "CLAUDE.md").write_text(
                base_text + _CHARTER_SEP + charter_tpl.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (role_dir / "settings.json").write_text(
                settings_tpl.read_text(encoding="utf-8").replace("<PROJECT_ROOT>", project_root_str),
                encoding="utf-8",
            )
        except OSError as exc:
            raise ProvisioningError(
                f"v2 charter provisioning failed (slug={slug}): writing {role_slug} charter: {exc}"
            ) from exc

    universal_tpl = NEX_STUDIO_TEMPLATES / _UNIVERSAL_CLAUDE_MD
    if not universal_tpl.is_file():
        raise ProvisioningError(
            f"v2 charter provisioning failed (slug={slug}): universal CLAUDE.md template missing at {universal_tpl}"
        )
    try:
        (project_root / "CLAUDE.md").write_text(
            universal_tpl.read_text(encoding="utf-8").replace("{{PROJECT_NAME}}", project_name),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ProvisioningError(
            f"v2 charter provisioning failed (slug={slug}): writing universal CLAUDE.md: {exc}"
        ) from exc

    # Remove the v1-only charter dirs (cosmetic clutter the engine never reads) — best-effort within this
    # hard step: a leftover dir does not block the build, so cleanup failure is swallowed, not raised.
    for v1_dir in _V1_AGENT_DIRS:
        shutil.rmtree(claude_dir / "agents" / v1_dir, ignore_errors=True)
    # Also drop the stale v1-role session-state files at the project root (.nex-{designer,implementer,
    # customer}-state.md) — a v2 project only has the ai-agent + auditor roles. Gitignored, disk-only
    # clutter; best-effort (a leftover file does not block the build).
    for v1_role in _V1_AGENT_DIRS:
        (project_root / f".nex-{v1_role}-state.md").unlink(missing_ok=True)

    # CR-V2-030: mark the project trusted so claude — the interactive "Surový terminál" OR the headless
    # dispatch — never hits its first-run "Do you trust this folder?" dialog. A NEX-Studio-created project
    # is trusted by construction. Best-effort (never raises); only sets the trust flag, never disables the
    # permission system.
    _mark_project_trusted(project_root)

    logger.info("v2 agent charters provisioned + project normalised to v2 shape (slug=%s)", slug)


def _mark_project_trusted(project_root: Path) -> None:
    """CR-V2-030: set ``hasTrustDialogAccepted=True`` for ``project_root`` in the claude CLI config
    (``$CLAUDE_CONFIG_DIR/.claude.json``), so neither a headless ``claude -p`` dispatch nor the interactive
    raw terminal blocks on claude's first-run "Do you trust this folder?" dialog.

    This is EXACTLY what clicking "Yes, I trust this folder" writes — it does NOT disable the permission
    system (the agent's ``settings.json`` allow/deny still apply; we never pass ``--dangerously-skip-
    permissions``). Best-effort: a missing / unreadable / unwritable config is logged, never raised (the
    headless build works regardless — only the interactive terminal would re-show the dialog). Idempotent:
    skips the write when already trusted. Atomic write (temp + ``os.replace``) so a crash can never
    truncate the shared config; the read-modify-write window is tiny but not locked (acceptable for a
    boolean flag — re-applied on the next provision if a concurrent claude write ever loses it)."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    config_path = Path(config_dir) / ".claude.json"
    if not config_path.is_file():
        logger.warning("trust mark SKIPPED — claude config not found at %s", config_path)
        return
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("trust mark SKIPPED — claude config unreadable (%s): %s", config_path, exc)
        return
    if not isinstance(data, dict):
        logger.warning("trust mark SKIPPED — claude config is not a JSON object (%s)", config_path)
        return
    projects = data.setdefault("projects", {})
    key = str(project_root)
    entry = projects.get(key)
    if not isinstance(entry, dict):
        entry = {}
        projects[key] = entry
    if entry.get("hasTrustDialogAccepted") is True:
        return  # already trusted — never rewrite the shared config needlessly
    entry["hasTrustDialogAccepted"] = True
    try:
        tmp = config_path.with_name(config_path.name + ".nexstudio-tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, config_path)
    except OSError as exc:
        logger.warning("trust mark write failed for %s: %s", project_root, exc)
        return
    logger.info("project marked trusted in claude config (%s)", project_root)


def run_post_scaffold_steps(
    *,
    target: str,
    slug: str,
    repo_url: str | None,
    project_type: str,
    auth_mode: str,
    enable_cicd: bool,
    full_smoke: bool,
    enable_branch_protection: bool,
) -> None:
    """Orchestrate archetype surface composition + K-004 (smoke) + K-005 (CI/CD) + branch protection.

    Best-effort — every step caught + logged as warning. Žiadny step nezdvíha
    HTTPException; partial success je acceptable (Manažér can finish manually).
    """
    target_path = Path(target) if target else None

    if target_path and target_path.is_dir():
        _compose_archetype_surfaces(target_path, slug, project_type=project_type, auth_mode=auth_mode)
        _run_smoke_test(target_path, slug, full=full_smoke)
        _seed_release_smoke_test(target_path, slug)
        # Commit + push the v2-shape normalisation (and archetype/smoke seeds) BEFORE the CI commit so the
        # fresh project has a clean working tree and the remote reflects the real v2 shape (not the v1
        # template it was bootstrapped from). Commit order: bootstrap → normalise → CI.
        _commit_and_push_scaffold_finalisation(target_path, slug)
    else:
        logger.warning("Skipping K-004 smoke test — target %r not a directory", target)

    if enable_cicd and target_path and target_path.is_dir():
        _wire_cicd_workflow(target_path, slug)
        _wire_precommit_hook(target_path)
        # The pushed ci.yml runs on ``andros-ubuntu-<slug>`` (self-hosted) — provision that runner now, else
        # every job queues forever (the nex-shopify gap, Director 2026-07-16). Best-effort, never raises.
        _provision_ci_runner(slug, repo_url)

    if enable_branch_protection and repo_url:
        _enable_branch_protection(repo_url, slug)


def _compose_archetype_surfaces(target: Path, slug: str, *, project_type: str, auth_mode: str) -> None:
    """CR-V2-005: compose the per-archetype SURFACE plan onto the scaffolded project.

    Standard = backend + a single app-FE surface (today's shape); Web = backend + an admin-FE surface +
    a public-site surface (the second FE surface). Both pick the login flavour from ``auth_mode``
    (``password``-login / ``token``-launch) for the backend + every surface.

    Best-effort and idempotent: the plan is recorded into ``.nex-archetype.json`` at the project root so
    the downstream scaffolder / engine knows which surfaces + login flavour to materialise. The detailed
    per-surface frontend trees are produced by the project scaffolder (init.sh ``--variant``); this step
    is the archetype CONTRACT, not the file-by-file emitter — keeping the surface plan in one place avoids
    a second source of truth diverging.

    Web commerce add-on (cart / checkout / payments + bidirectional IS-integration) is DEFERRED
    (OQ-11, §7 Open #11): NO commerce/cart/checkout/payment artifact is emitted here. The recorded plan
    carries an explicit, documented ``commerce`` extension seam (always ``false`` / ``deferred`` in
    v2.0.0) so the future commerce design round has a defined attachment point without any code today.
    """
    surfaces = _ARCHETYPE_SURFACES.get(project_type)
    if surfaces is None:
        # Unknown/deferred archetype (e.g. a future ``mobil``) — never guess a composition.
        logger.warning(
            "Archetype surface composition SKIPPED — unknown project type %r (slug=%s)",
            project_type,
            slug,
        )
        return

    plan = {
        "type": project_type,
        "auth_mode": auth_mode,
        "surfaces": [{"name": name, "kind": "frontend", "auth_mode": auth_mode} for name in surfaces],
        "backend": {"auth_mode": auth_mode},
        # DEFERRED extension seam (OQ-11): the Web commerce add-on attaches here in a future design
        # round. v2.0.0 emits NO commerce code — this is a documented placeholder, never enabled.
        "commerce": {"enabled": False, "status": "deferred"},
    }

    dest = target / ".nex-archetype.json"
    try:
        dest.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("Archetype surface plan write failed (slug=%s): %s", slug, exc)
        return

    logger.info(
        "Archetype surfaces composed (slug=%s, type=%s, auth_mode=%s, surfaces=%s)",
        slug,
        project_type,
        auth_mode,
        ",".join(surfaces),
    )


def _compose_backend_published_port(compose_file: Path) -> int | None:
    """The HOST-published port of the ``backend`` service (first ``ports`` entry) — the target for
    the host-side ``curl`` health probe (which hits the *published* port, not the container port).

    Handles the short forms (``"host:container"`` / ``"ip:host:container"``, optional ``/proto``) and
    the long form (``{published: …}``). Returns ``None`` when undeterminable (no ``backend`` service /
    no ``ports`` / a bare ``"container"`` entry with no host publish / unparseable) so the caller
    SKIPS the probe rather than hit a wrong hardcoded port (the K-004 ``localhost:8000`` bug — IPv6
    localhost + a guessed port both false-fail; nginx/uvicorn bind IPv4 on the derived port)."""
    try:
        data = yaml.safe_load(compose_file.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return None
    backend = (data.get("services") or {}).get("backend") or {}
    ports = backend.get("ports") or []
    if not ports:
        return None
    entry = ports[0]
    if isinstance(entry, dict):  # long syntax: {target: 8000, published: 9110}
        published = entry.get("published")
        if isinstance(published, int):
            return published
        return int(published) if isinstance(published, str) and published.isdigit() else None
    # short syntax: "9110:8000" / "127.0.0.1:9110:8000" / "8000" — the host port is the
    # second-to-last colon segment; a bare "8000" (no host publish) has no deterministic host port.
    segments = str(entry).split("/", 1)[0].split(":")
    if len(segments) < 2:
        return None
    host = segments[-2]
    return int(host) if host.isdigit() else None


def _run_smoke_test(target: Path, slug: str, *, full: bool) -> None:
    """K-004: docker compose build (minimal) alebo build + up + health (full)."""
    compose_file = target / "docker-compose.yml"
    if not compose_file.is_file():
        logger.info(
            "K-004 smoke test SKIPPED — no docker-compose.yml in %s (slug=%s)",
            target,
            slug,
        )
        return

    logger.info("K-004 smoke test starting (slug=%s, full=%s)", slug, full)

    # Minimal smoke: docker compose build (always run)
    build_result = subprocess.run(
        ["docker", "compose", "build"],
        cwd=str(target),
        capture_output=True,
        text=True,
        timeout=SMOKE_FULL_TIMEOUT if full else SMOKE_BUILD_TIMEOUT,
        check=False,
    )
    if build_result.returncode != 0:
        # Log stderr tail; don't raise — best-effort
        stderr_tail = "\n".join(build_result.stderr.strip().splitlines()[-10:])
        logger.warning(
            "K-004 smoke test FAIL (slug=%s, exit=%d): %s",
            slug,
            build_result.returncode,
            stderr_tail,
        )
        return

    if not full:
        logger.info("K-004 minimal smoke test PASS (slug=%s)", slug)
        return

    # Full smoke: up -d, wait healthy, health endpoint, then down -v
    try:
        up_result = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=str(target),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if up_result.returncode != 0:
            stderr_tail = "\n".join(up_result.stderr.strip().splitlines()[-5:])
            logger.warning("K-004 full smoke 'up' FAIL (slug=%s): %s", slug, stderr_tail)
            return

        # Best-effort health check against the DERIVED backend host port on 127.0.0.1 (IPv4) — never
        # the hardcoded ``localhost:8000`` (IPv6 ``localhost`` + a guessed port both false-fail;
        # nginx/uvicorn bind IPv4 on the published port). If the port is undeterminable, skip the
        # probe (best-effort) rather than hit a wrong port.
        backend_port = _compose_backend_published_port(compose_file)
        if backend_port is None:
            logger.info(
                "K-004 full smoke /health probe SKIPPED — no derivable backend host port (slug=%s)",
                slug,
            )
        else:
            health_url = f"http://127.0.0.1:{backend_port}/health"
            for _attempt in range(6):
                health = subprocess.run(
                    ["curl", "-sf", health_url],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if health.returncode == 0:
                    logger.info("K-004 full smoke /health endpoint OK (slug=%s, port=%d)", slug, backend_port)
                    break
                subprocess.run(["sleep", "5"], check=False)
            else:
                logger.warning(
                    "K-004 full smoke /health endpoint not reachable in 30s (slug=%s, url=%s)",
                    slug,
                    health_url,
                )
    finally:
        # Cleanup — always run docker compose down -v even if up failed
        subprocess.run(
            ["docker", "compose", "down", "-v"],
            cwd=str(target),
            capture_output=True,
            timeout=60,
            check=False,
        )

    logger.info("K-004 full smoke test PASS (slug=%s)", slug)


def _seed_release_smoke_test(target: Path, slug: str) -> None:
    """gate-g-hardening GAP 1 (CR-B): seed ``release_smoke_test.sh`` into the new project (mirrors the
    K-005 CI copy-pattern). The engine runs it at full-flow gate_g as the behavioural release-acceptance
    gate; a web app without it FAILs the gate ("required but missing"). Best-effort — a missing template or
    a copy error is logged as a warning, never raised (the Manažér can add the script manually). Idempotent:
    an existing project script is preserved (never clobber a hand-tuned acceptance suite)."""
    if not RELEASE_SMOKE_TEMPLATE.is_file():
        logger.warning("release_smoke_test.sh seed SKIPPED — template missing at %s", RELEASE_SMOKE_TEMPLATE)
        return

    dest = target / "release_smoke_test.sh"
    if dest.is_file():
        logger.info("release_smoke_test.sh seed SKIPPED — already exists (slug=%s)", slug)
        return

    try:
        shutil.copy2(RELEASE_SMOKE_TEMPLATE, dest)
        dest.chmod(dest.stat().st_mode | 0o111)  # ensure +x (the engine runs it via ``bash``, but keep it executable)
    except OSError as exc:
        logger.warning("release_smoke_test.sh seed failed (slug=%s): %s", slug, exc)
        return

    logger.info("release_smoke_test.sh seeded (slug=%s)", slug)


def _seed_ci_render_helper(target: Path, slug: str) -> None:
    """CR-R2-3 (#3): seed ``scripts/ci_render_dotenv.py`` into the new project (mirrors the
    ``_seed_release_smoke_test`` copy-pattern). The CI ``migrate`` job runs it to render a CI ``.env`` from
    ``.env.example`` with the ``DATABASE_URL`` scheme preserved verbatim (the ``+pg8000`` driver is what the
    online alembic migrate exercises). Best-effort — a missing template or a copy error is logged as a
    warning, never raised. Idempotent: an existing project helper is preserved (never clobber a hand-tuned
    one)."""
    if not CI_RENDER_HELPER_TEMPLATE.is_file():
        logger.warning("ci_render_dotenv.py seed SKIPPED — template missing at %s", CI_RENDER_HELPER_TEMPLATE)
        return

    scripts_dir = target / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    dest = scripts_dir / "ci_render_dotenv.py"
    if dest.is_file():
        logger.info("ci_render_dotenv.py seed SKIPPED — already exists (slug=%s)", slug)
        return

    try:
        shutil.copy2(CI_RENDER_HELPER_TEMPLATE, dest)
        dest.chmod(dest.stat().st_mode | 0o111)  # +x (CI invokes it via `python3`, but keep it executable)
    except OSError as exc:
        logger.warning("ci_render_dotenv.py seed failed (slug=%s): %s", slug, exc)
        return

    logger.info("ci_render_dotenv.py seeded (slug=%s)", slug)


def _wire_precommit_hook(target: Path) -> None:
    """Install + activate the ruff / type-check pre-commit hook in a new project (v4.0.29).

    Blocks locally any commit that the CI Lint stage would reject, so the AI Agent can never push
    known-red code (the root of the recurring 'CI / lint Failed' on generated projects). Copies the
    hook template to ``.githooks/pre-commit`` (executable), tracks it in the repo, and points the
    workdir clone at it via ``git config core.hooksPath .githooks``. Best-effort: a missing template
    or a git failure is logged, never raised (mirrors :func:`_wire_cicd_workflow`)."""
    if not PRECOMMIT_HOOK_TEMPLATE.is_file():
        logger.warning("pre-commit hook wire-up SKIPPED — template missing at %s", PRECOMMIT_HOOK_TEMPLATE)
        return

    hooks_dir = target / ".githooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "pre-commit"
    hook.write_text(PRECOMMIT_HOOK_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    hook.chmod(0o755)

    # Activate for the workdir clone (core.hooksPath is per-clone; the file is committed so it travels).
    subprocess.run(
        ["git", "-C", str(target), "config", "core.hooksPath", ".githooks"],
        capture_output=True,
        text=True,
        timeout=CICD_TIMEOUT,
        check=False,
    )
    subprocess.run(
        ["git", "-C", str(target), "add", ".githooks/pre-commit"],
        capture_output=True,
        text=True,
        timeout=CICD_TIMEOUT,
        check=False,
    )
    commit = subprocess.run(
        ["git", "-C", str(target), "commit", "-m", "chore: pre-commit ruff/type-check hook (block known-red commits)"],
        capture_output=True,
        text=True,
        timeout=CICD_TIMEOUT,
        check=False,
    )
    if commit.returncode != 0:
        logger.info("pre-commit hook commit skipped/failed (slug workdir): %s", commit.stderr.strip()[:200])


def _wire_cicd_workflow(target: Path, slug: str) -> None:
    """K-005: render the CI template (substitute ``{{PROJECT_SLUG}}`` → self-hosted ``runs-on``), seed the
    ``migrate`` job's dotenv helper, commit + push.

    CR-R2-3 (#3): the template is RENDERED, not flat-copied — its ``runs-on`` carries a ``{{PROJECT_SLUG}}``
    token that becomes ``andros-ubuntu-<slug>`` (ICC D-009: all CI on self-hosted runners). The ``migrate``
    job runs ``scripts/ci_render_dotenv.py``, so that helper is seeded + committed alongside ``ci.yml``."""
    if not CICD_TEMPLATE.is_file():
        logger.warning(
            "K-005 CI/CD wire-up SKIPPED — template missing at %s",
            CICD_TEMPLATE,
        )
        return

    workflows_dir = target / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    ci_yml = workflows_dir / "ci.yml"

    if ci_yml.is_file():
        logger.info("K-005 CI/CD SKIPPED — ci.yml already exists (slug=%s)", slug)
        return

    # Render (not flat-copy): substitute the {{PROJECT_SLUG}} token so runs-on targets the project's
    # registered self-hosted label (andros-ubuntu-<slug>). A naive literal replace leaves GitHub's own
    # `${{ … }}` / Go-template `{{.State.…}}` expressions untouched (the token is the exact {{PROJECT_SLUG}}).
    rendered = CICD_TEMPLATE.read_text(encoding="utf-8").replace("{{PROJECT_SLUG}}", slug)
    ci_yml.write_text(rendered, encoding="utf-8")

    # Seed the migrate job's dotenv helper so the CI `migrate` step (`python3 scripts/ci_render_dotenv.py`)
    # has it in the repo. Committed below alongside ci.yml only if it actually landed (best-effort seed).
    _seed_ci_render_helper(target, slug)
    paths_to_add = [".github/workflows/ci.yml"]
    if (target / "scripts" / "ci_render_dotenv.py").is_file():
        paths_to_add.append("scripts/ci_render_dotenv.py")

    # Commit + push
    add_result = subprocess.run(
        ["git", "-C", str(target), "add", *paths_to_add],
        capture_output=True,
        text=True,
        timeout=CICD_TIMEOUT,
        check=False,
    )
    if add_result.returncode != 0:
        logger.warning(
            "K-005 git add failed (slug=%s): %s",
            slug,
            add_result.stderr.strip(),
        )
        return

    commit_result = subprocess.run(
        ["git", "-C", str(target), "commit", "-m", "feat(ci): initial CI workflow from NEX Studio template"],
        capture_output=True,
        text=True,
        timeout=CICD_TIMEOUT,
        check=False,
    )
    if commit_result.returncode != 0:
        logger.warning(
            "K-005 git commit failed (slug=%s): %s",
            slug,
            commit_result.stderr.strip(),
        )
        return

    push_result = subprocess.run(
        ["git", "-C", str(target), "push", "origin", "main"],
        capture_output=True,
        text=True,
        timeout=CICD_TIMEOUT,
        check=False,
    )
    if push_result.returncode != 0:
        logger.warning(
            "K-005 git push failed (slug=%s): %s — CI committed locally, push deferred",
            slug,
            push_result.stderr.strip(),
        )
        return

    logger.info("K-005 CI/CD workflow committed + pushed (slug=%s)", slug)


def _provision_ci_runner(slug: str, repo_url: str | None) -> None:
    """Auto-provision a containerized self-hosted GitHub Actions runner for the new repo (Director 2026-07-16).

    Closes the "CI pushed but no runner → every job queues forever" gap: :func:`_wire_cicd_workflow` pushes a
    ci.yml whose jobs ``runs-on: andros-ubuntu-<slug>`` (self-hosted), but nothing registered such a runner.
    The 13 legacy runners are HOST systemd services; the NEX Studio backend runs INSIDE a container with no
    ``systemctl`` / ``sudo`` / host runner dir, so it cannot install one that way. It CAN, via the mounted
    ``/var/run/docker.sock``, run the runner as a Docker container — the only zero-touch path from the cockpit.

    ``myoung34/github-runner`` self-registers from the backend's GH PAT and bundles the docker CLI; the host
    docker.sock is mounted in so the CI's ``docker compose build`` + service-container jobs execute against the
    host daemon exactly like the systemd runners do. Persistent + self-healing (``--restart unless-stopped``).

    Best-effort: any failure is logged, never raised (the Manažér can register a runner manually — the pushed
    ci.yml still stands). Idempotent: skips when a runner container for this slug already exists. The PAT is
    passed to ``docker`` via the child ENV (``-e ACCESS_TOKEN`` name-only), never on argv, so it never lands in
    ``ps`` / logs.
    """
    from backend.services.template_bootstrap import _repo_from_url

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        logger.warning("CI runner provisioning SKIPPED — no GITHUB_TOKEN/GH_TOKEN in env (slug=%s)", slug)
        return

    repo_full = _repo_from_url(repo_url, slug)  # owner/repo
    container = f"nex-ci-runner-{slug}"
    label = f"andros-ubuntu-{slug}"

    # Idempotent — a runner container for this slug already present (running or stopped) → leave it.
    existing = subprocess.run(
        ["docker", "ps", "-aq", "-f", f"name=^{container}$"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if existing.returncode == 0 and existing.stdout.strip():
        logger.info("CI runner provisioning SKIPPED — container %s already exists (slug=%s)", container, slug)
        return

    # Pass the PAT via the child ENV (``-e ACCESS_TOKEN`` name-only) so it is NEVER on argv (ps/logs).
    child_env = {**os.environ, "ACCESS_TOKEN": token}
    run = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "--restart",
            "unless-stopped",
            "-e",
            "ACCESS_TOKEN",
            "-e",
            f"REPO_URL=https://github.com/{repo_full}",
            "-e",
            "RUNNER_SCOPE=repo",
            "-e",
            f"RUNNER_NAME={label}",
            "-e",
            f"LABELS={label}",
            "-e",
            "DISABLE_AUTO_UPDATE=true",
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock",
            CI_RUNNER_IMAGE,
        ],
        capture_output=True,
        text=True,
        timeout=CI_RUNNER_PROVISION_TIMEOUT,
        check=False,
        env=child_env,
    )
    if run.returncode != 0:
        logger.warning("CI runner provisioning FAILED (slug=%s): %s", slug, run.stderr.strip())
        return
    logger.info("CI runner container provisioned (slug=%s, label=%s)", slug, label)


def deprovision_ci_runner(slug: str) -> None:
    """Best-effort teardown of the containerized CI runner for a deleted project (Director 2026-07-16).

    Removes the ``nex-ci-runner-<slug>`` container (``docker rm -f``); GitHub then shows the runner offline
    (reclaimed by a same-named ``--replace`` re-provision on re-create). Never raises — mirrors the UAT / KB /
    RAG teardowns in the delete route. A no-op when no such container exists (project predates this feature or
    had CI disabled)."""
    container = f"nex-ci-runner-{slug}"
    result = subprocess.run(
        ["docker", "rm", "-f", container],
        capture_output=True,
        text=True,
        timeout=CI_RUNNER_TEARDOWN_TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        logger.info("CI runner teardown — nothing removed for slug=%s (%s)", slug, result.stderr.strip())
        return
    logger.info("CI runner container removed (slug=%s)", slug)


def _commit_and_push_scaffold_finalisation(target: Path, slug: str) -> None:
    """Commit + push any residual scaffold changes the earlier steps wrote but never committed — chiefly the
    v2-shape normalisation from :func:`provision_v2_agent_charters` (rewritten root ``CLAUDE.md`` + ai-agent/
    auditor charters + removed v1 agent dirs) and the archetype-surface metadata. Without this the freshly
    created project keeps a DIRTY working tree and the remote repo still shows the v1 template shape.

    Best-effort (never raises; a git failure is logged and the project stays usable). ``git add -A`` respects
    the project ``.gitignore`` so gitignored artifacts (``.env``, ``.nex-*-state.md``, ``MEMORY.md``) are never
    staged. No-op when nothing is staged (tree already clean)."""
    add_result = subprocess.run(
        ["git", "-C", str(target), "add", "-A"],
        capture_output=True,
        text=True,
        timeout=CICD_TIMEOUT,
        check=False,
    )
    if add_result.returncode != 0:
        logger.warning("scaffold finalise git add failed (slug=%s): %s", slug, add_result.stderr.strip())
        return

    # Nothing staged → the working tree is already clean, no residual commit needed.
    diff_result = subprocess.run(
        ["git", "-C", str(target), "diff", "--cached", "--quiet"],
        capture_output=True,
        text=True,
        timeout=CICD_TIMEOUT,
        check=False,
    )
    if diff_result.returncode == 0:
        logger.info("scaffold finalise — working tree already clean, nothing to commit (slug=%s)", slug)
        return

    commit_result = subprocess.run(
        ["git", "-C", str(target), "commit", "-m", "chore(scaffold): normalise project to v2 agent shape"],
        capture_output=True,
        text=True,
        timeout=CICD_TIMEOUT,
        check=False,
    )
    if commit_result.returncode != 0:
        logger.warning("scaffold finalise git commit failed (slug=%s): %s", slug, commit_result.stderr.strip())
        return

    push_result = subprocess.run(
        ["git", "-C", str(target), "push", "origin", "main"],
        capture_output=True,
        text=True,
        timeout=CICD_TIMEOUT,
        check=False,
    )
    if push_result.returncode != 0:
        logger.warning(
            "scaffold finalise git push failed (slug=%s): %s — committed locally, push deferred",
            slug,
            push_result.stderr.strip(),
        )
        return

    logger.info("scaffold finalise — v2-shape normalisation committed + pushed (slug=%s)", slug)


def _enable_branch_protection(repo_url: str, slug: str) -> None:
    """O-3: configure GitHub branch protection (require PR, no force push)."""
    from backend.services.template_bootstrap import _repo_from_url

    repo_full_name = _repo_from_url(repo_url, slug)

    # gh CLI: PUT /repos/{owner}/{repo}/branches/main/protection
    # Minimal protection: require PR review + no force push.
    api_path = f"repos/{repo_full_name}/branches/main/protection"
    # See https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection
    args = [
        "gh",
        "api",
        "--method",
        "PUT",
        api_path,
        "-f",
        "required_status_checks=null",
        "-F",
        "enforce_admins=false",
        "-f",
        "required_pull_request_reviews[required_approving_review_count]=1",
        "-f",
        "restrictions=null",
        "-F",
        "allow_force_pushes=false",
        "-F",
        "allow_deletions=false",
    ]
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=BRANCH_PROTECTION_TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        logger.warning(
            "Branch protection setup failed (repo=%s): %s — Manažér can configure manually",
            repo_full_name,
            result.stderr.strip(),
        )
        return
    logger.info("Branch protection enabled (repo=%s)", repo_full_name)

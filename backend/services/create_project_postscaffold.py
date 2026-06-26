"""F-004 Stage 5+6: K-004 smoke test + K-005 CI/CD wire-up + branch protection.

Both stages sú best-effort — partial success acceptable. Failure logged ako
warning, NIE 500. Manažér môže re-run / wire manually ak treba.

Per F-004 spec §3.4 + §3.5 + spec O-3 (branch protection opt-in).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

NEX_STUDIO_TEMPLATES = Path("/opt/projects/nex-studio/templates")
CICD_TEMPLATE = NEX_STUDIO_TEMPLATES / "github-actions-workflow.yml"
# gate-g-hardening GAP 1 (CR-B): the behavioural release-acceptance script the engine runs at gate_g.
RELEASE_SMOKE_TEMPLATE = NEX_STUDIO_TEMPLATES / "release_smoke_test.sh"
# CR-R2-3 (#3): the CI `migrate` job's dotenv renderer — shipped alongside ci.yml so the gate can run.
CI_RENDER_HELPER_TEMPLATE = NEX_STUDIO_TEMPLATES / "ci_render_dotenv.py"
SMOKE_BUILD_TIMEOUT = 300  # 5 min — minimal smoke is docker compose build only
SMOKE_FULL_TIMEOUT = 600  # 10 min — full smoke incl up + health
CICD_TIMEOUT = 60
BRANCH_PROTECTION_TIMEOUT = 30


def run_post_scaffold_steps(
    *,
    target: str,
    slug: str,
    repo_url: str | None,
    enable_cicd: bool,
    full_smoke: bool,
    enable_branch_protection: bool,
) -> None:
    """Orchestrate K-004 (smoke) + K-005 (CI/CD) + branch protection post-scaffold.

    Best-effort — every step caught + logged as warning. Žiadny step nezdvíha
    HTTPException; partial success je acceptable (Manažér can finish manually).
    """
    target_path = Path(target) if target else None

    if target_path and target_path.is_dir():
        _run_smoke_test(target_path, slug, full=full_smoke)
        _seed_release_smoke_test(target_path, slug)
    else:
        logger.warning("Skipping K-004 smoke test — target %r not a directory", target)

    if enable_cicd and target_path and target_path.is_dir():
        _wire_cicd_workflow(target_path, slug)

    if enable_branch_protection and repo_url:
        _enable_branch_protection(repo_url, slug)


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

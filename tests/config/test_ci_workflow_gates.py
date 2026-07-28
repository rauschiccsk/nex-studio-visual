"""Guards for the CI gates themselves — a gate that cannot fail is not a gate.

Two classes of rot are pinned here, both found by the 2026-07-28 audit:

1. A check that exists but nothing runs. ESLint was fully configured (``eslint.config.js``, two
   plugins, an npm script) and executed by no gate at all, while the cockpit forced ``npm run lint``
   on every project it generated.
2. A step that runs but cannot go red. The deploy health check ended in
   ``|| echo "Health check pending"``, throwing away the exit code it had just computed.

Both are invisible in a green build, so they need a test that reads the workflow itself.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PACKAGE_JSON = REPO_ROOT / "frontend" / "package.json"
PRE_COMMIT_HOOK = REPO_ROOT / ".githooks" / "pre-commit"

# Warning baseline frozen into ``frontend/package.json`` (``eslint . --max-warnings N``). The cap is
# a RATCHET: fixing warnings lowers it, nothing may raise it. Raising the cap to turn a red build
# green is a threshold downgrade, and this number is what makes that visible in review.
ESLINT_MAX_WARNINGS_BASELINE = 5

# Shell shapes that make a failure unable to propagate. Anything matching these inside a step's
# ``run:`` block must be listed in ALLOWED_MUTED_STEPS with a reason.
MUTED_FAILURE_PATTERNS = (
    "|| true",
    "|| echo",
    "|| :",
    "2>/dev/null",
)

# (job id, step name) -> why muting a failure there is correct.
ALLOWED_MUTED_STEPS = {
    ("build", "Clean up CI images"): (
        "best-effort cleanup under `if: always()` — the image may already be gone, and a failed "
        "`docker rmi` must not turn a green build red"
    ),
    ("deploy", "Deploy with Docker Compose"): (
        "`docker compose down` on a first deploy has nothing to bring down; every other command in "
        "the step propagates under `set -euo pipefail`"
    ),
}


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _iter_steps():
    """Yield ``(job_id, step)`` for every step of every job in the CI workflow."""
    for job_id, job in _load_workflow()["jobs"].items():
        for step in job.get("steps", []) or []:
            yield job_id, step


def _strip_shell_comments(script: str) -> str:
    """Drop ``#`` comments so prose about ``|| true`` is not mistaken for ``|| true``."""
    out = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        out.append(re.sub(r"\s#.*$", "", line))
    return "\n".join(out)


def _step(job_id: str, name: str) -> dict:
    for candidate_job, step in _iter_steps():
        if candidate_job == job_id and step.get("name") == name:
            return step
    pytest.fail(f"CI workflow has no step named {name!r} in job {job_id!r}")


class TestEslintGate:
    """ESLint must be executed by CI, not merely configured."""

    def test_ci_runs_eslint_on_the_frontend(self):
        commands = [step.get("run", "") for _job, step in _iter_steps() if step.get("working-directory") == "frontend"]
        assert any("npm run lint" in cmd for cmd in commands), (
            "no CI step runs `npm run lint` — eslint is configured but ungated again; "
            "the cockpit forces this gate on every project it creates, so this repo runs it too"
        )

    def test_lint_script_caps_warnings(self):
        scripts = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["scripts"]
        lint = scripts["lint"]
        match = re.search(r"--max-warnings[= ](\d+)", lint)
        assert match, f"`lint` script ({lint!r}) has no --max-warnings cap — warnings would pile up invisibly again"
        assert int(match.group(1)) <= ESLINT_MAX_WARNINGS_BASELINE, (
            f"--max-warnings was raised to {match.group(1)} (baseline "
            f"{ESLINT_MAX_WARNINGS_BASELINE}). The cap is a ratchet: fix the warning instead of "
            "lifting the threshold. Lowering it is welcome — update the baseline here too."
        )

    def test_pre_commit_hook_runs_the_same_command(self):
        hook = PRE_COMMIT_HOOK.read_text(encoding="utf-8")
        assert "npm run lint" in hook, (
            "the pre-commit hook stopped running eslint — local checks must match the CI gate, "
            "otherwise the failure is discovered only after a push"
        )


class TestDeployHealthCheck:
    """The health check must be able to fail — it is the only proof the deploy worked."""

    def test_health_check_does_not_swallow_its_exit_code(self):
        body = _strip_shell_comments(_step("deploy", "Health check")["run"])
        for muted in ("|| echo", "|| true", "|| :"):
            assert muted not in body, (
                f"health check swallows its exit code again ({muted!r}) — a dead backend would report a green deploy"
            )

    def test_health_check_actually_inspects_service_health(self):
        body = _strip_shell_comments(_step("deploy", "Health check")["run"])
        assert "docker inspect" in body and "State.Health" in body, (
            "the health check no longer reads the compose-declared health of the deployed services"
        )
        # The host publishes the backend on 9213, not the in-container 9176 that
        # backend/scripts/healthcheck.py probes — that script is the Dockerfile HEALTHCHECK, not a
        # host-side gate. Using it here again would make every deploy red.
        assert "backend.scripts.healthcheck" not in body, (
            "host-side health check must not run the in-container probe (wrong port, always fails)"
        )

    def test_health_check_fails_the_job_when_a_service_is_unhealthy(self):
        body = _strip_shell_comments(_step("deploy", "Health check")["run"])
        assert "exit 1" in body, "health check has no failing exit path"
        assert "::error::" in body, "a failing health check must say why in the run annotation"


class TestNoStepIsUnfailable:
    """Sweep every step for shapes whose failure cannot propagate."""

    def test_no_step_is_marked_continue_on_error(self):
        offenders = [
            f"{job}/{step.get('name', step.get('uses', '?'))}"
            for job, step in _iter_steps()
            if step.get("continue-on-error")
        ]
        assert not offenders, f"steps that cannot fail the job: {offenders}"

    def test_no_job_is_marked_continue_on_error(self):
        jobs = _load_workflow()["jobs"]
        offenders = [job_id for job_id, job in jobs.items() if job.get("continue-on-error")]
        assert not offenders, f"jobs that cannot fail the workflow: {offenders}"

    def test_muted_failures_are_only_the_documented_ones(self):
        offenders = []
        for job_id, step in _iter_steps():
            body = _strip_shell_comments(step.get("run", "") or "")
            hits = [pattern for pattern in MUTED_FAILURE_PATTERNS if pattern in body]
            if not hits:
                continue
            key = (job_id, step.get("name", ""))
            if key in ALLOWED_MUTED_STEPS:
                continue
            offenders.append(f"{job_id}/{key[1]}: {hits}")
        assert not offenders, (
            "step(s) discard a failure without justification: "
            + "; ".join(offenders)
            + " — either let the command fail, or add the step to ALLOWED_MUTED_STEPS with a reason"
        )

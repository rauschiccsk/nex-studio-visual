"""DEV-38 — the CI runner is up and online on GitHub BEFORE the CI workflow is pushed.

Found 09.10.2026 on Career Asistent (account alex): founding pushed ``ci.yml`` first and started the runner after
it. GitHub queued the first run at 10:04:42Z, the runner was listening at 10:04:47Z — and the job started only at
10:09:26Z. On dedo-home (01.10.2026) the first run never started and GitHub cancelled it after exactly 24 hours.

Pinned here:
* the runner is started, and waited for until GitHub reports it online, before ``ci.yml`` is pushed;
* a runner that never comes online, or a GitHub that cannot be asked, is said in the founding warnings — and the
  CI workflow is pushed anyway (a project must not be left without CI);
* a runner that cannot even be started does not stop the chain either.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from backend.services import create_project_postscaffold as mod
from backend.services import github_validation

NEVER_ONLINE = "Vykonávač kontrol sa do 120 s neprihlásil na GitHube — prvá kontrola počká, kým sa prihlási."
CANNOT_ASK = "Nepodarilo sa overiť, či je vykonávač kontrol prihlásený na GitHube — prvá kontrola môže čakať."
NOT_STARTED = "Vykonávač kontrol sa nepodarilo spustiť — kontroly počkajú, kým ho niekto spustí."


def _run_chain(tmp_path: Path, order: list[str], *, runner_side_effect=None, wait_note=None) -> list[str]:
    def _rec(name, result=None, side_effect=None):
        def _f(*args, **kwargs):
            order.append(name)
            if side_effect is not None:
                raise side_effect
            return result

        return _f

    with (
        patch.object(mod, "_compose_archetype_surfaces", _rec("surfaces")),
        patch.object(mod, "_run_smoke_test", _rec("smoke")),
        patch.object(mod, "_seed_release_smoke_test", _rec("release-smoke")),
        patch.object(mod, "_commit_and_push_scaffold_finalisation", _rec("push-scaffold")),
        patch.object(mod, "_provision_ci_runner", _rec("runner", side_effect=runner_side_effect)),
        patch.object(mod, "_wait_for_ci_runner", _rec("wait-runner", result=wait_note)),
        patch.object(mod, "_wire_cicd_workflow", _rec("push-ci")),
        patch.object(mod, "_wire_precommit_hook", _rec("hook")),
    ):
        return mod.run_post_scaffold_steps(
            target=str(tmp_path),
            slug="career-asistent",
            repo_url="rauschiccsk/career-asistent",
            project_type="standard",
            auth_mode="password",
            enable_cicd=True,
            full_smoke=False,
            enable_branch_protection=False,
        )


def test_the_runner_is_online_before_ci_yml_is_pushed(tmp_path: Path) -> None:
    order: list[str] = []
    warnings = _run_chain(tmp_path, order)

    assert order == ["surfaces", "smoke", "release-smoke", "push-scaffold", "runner", "wait-runner", "push-ci", "hook"]
    assert warnings == []


def test_a_runner_that_never_comes_online_is_said_and_ci_is_pushed_anyway(tmp_path: Path) -> None:
    order: list[str] = []
    warnings = _run_chain(tmp_path, order, wait_note=NEVER_ONLINE)

    assert warnings == [NEVER_ONLINE]
    assert order[-2:] == ["push-ci", "hook"]


def test_a_runner_that_cannot_be_started_is_said_and_ci_is_pushed_anyway(tmp_path: Path) -> None:
    order: list[str] = []
    warnings = _run_chain(tmp_path, order, runner_side_effect=FileNotFoundError("docker"))

    assert warnings == [NOT_STARTED]
    assert "wait-runner" not in order  # nothing was started, so there is nothing to wait for
    assert order[-2:] == ["push-ci", "hook"]


def _statuses(*answers):
    seq = list(answers)
    calls = MagicMock()

    def _status(repo_full, label, *, timeout):
        calls(repo_full, label)
        answer = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return _status, calls


def test_waiting_ends_as_soon_as_github_reports_the_runner_online(monkeypatch: pytest.MonkeyPatch) -> None:
    status, calls = _statuses(None, "offline", "online")
    monkeypatch.setattr(github_validation, "runner_status", status)
    clock = iter(range(0, 100_000, 3))  # a stand-in clock: a wait that misses „online" fails fast, not in 2 min
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    assert mod._wait_for_ci_runner("career-asistent", "rauschiccsk/career-asistent", wait=120, poll=3) is None
    assert calls.call_count == 3
    assert calls.call_args.args == ("rauschiccsk/career-asistent", "andros-ubuntu-career-asistent")


def test_a_runner_still_offline_when_the_wait_runs_out_is_said(monkeypatch: pytest.MonkeyPatch) -> None:
    status, calls = _statuses("offline")
    monkeypatch.setattr(github_validation, "runner_status", status)
    clock = iter(range(0, 1000, 50))
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    assert mod._wait_for_ci_runner("career-asistent", "rauschiccsk/career-asistent", wait=120, poll=3) == NEVER_ONLINE
    assert 2 <= calls.call_count <= 4  # polled until the deadline, not forever


def test_a_github_that_cannot_be_asked_is_said_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    status, calls = _statuses(RuntimeError("GitHub API returned unexpected status 401"))
    monkeypatch.setattr(github_validation, "runner_status", status)
    monkeypatch.setattr(mod.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("kept polling")))

    assert mod._wait_for_ci_runner("career-asistent", "rauschiccsk/career-asistent", wait=120, poll=3) == CANNOT_ASK
    assert calls.call_count == 1


def _github(status_code: int, body: dict) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = body
    response.text = str(body)
    return response


def test_runner_status_reads_the_named_runner_from_github(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "total_count": 2,
        "runners": [
            {"name": "andros-ubuntu-other", "status": "online"},
            {"name": "andros-ubuntu-career-asistent", "status": "offline"},
        ],
    }
    seen: dict[str, str] = {}

    def _get(url, headers, timeout):
        seen["url"] = url
        return _github(200, body)

    monkeypatch.setattr(github_validation.httpx, "get", _get)

    assert github_validation.runner_status(
        "rauschiccsk/career-asistent", "andros-ubuntu-career-asistent", timeout=5
    ) == ("offline")
    assert seen["url"] == f"{github_validation.GITHUB_API_BASE}/repos/rauschiccsk/career-asistent/actions/runners"
    assert github_validation.runner_status("rauschiccsk/career-asistent", "andros-ubuntu-missing", timeout=5) is None


def test_runner_status_refuses_to_guess_on_an_unexpected_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_validation.httpx, "get", lambda url, headers, timeout: _github(401, {"message": "x"}))

    with pytest.raises(RuntimeError, match="401"):
        github_validation.runner_status("rauschiccsk/career-asistent", "andros-ubuntu-career-asistent", timeout=5)

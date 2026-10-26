"""DEV-226 — a started CI runner counts as started only once its own log says it listens for jobs.

26.10.2026 the cockpit converted nex-websites' runners and reported „prevedený … na GitHube online“ at 04:02. GitHub
still held the replaced runners' sessions: the new ones logged „Listening for Jobs“ only at 04:03:25 and 04:04:25. A
rerun queued at 04:02:29 waited 14 minutes for nobody, until it was cancelled; run again, it was taken in 6 s. Director
26.10.2026: „Áno, súhlasím, založ tikety a implementuj“.

Pinned: provision returns only after every runner it started has logged the line since its own start (a line from
before a restart does not count); a runner that has listened since its start holds nothing; one that gets no session
in ``settings.ci_runner_session_wait_seconds`` is named and the conversion does not report it converted.
"""

from __future__ import annotations

import subprocess

import pytest

from backend.config.settings import settings
from backend.scripts import ci_runners
from backend.services import ci_runner, github_validation
from tests.services.test_a_project_gets_several_ci_runners import STARTED_AT, TOKEN, _Completed

SLUG = "nex-demo"
RUNNER = "nex-ci-runner-nex-demo"
OLD_SESSION = f"2026-10-25 22:00:00Z: {ci_runner.LISTENING}\n"  # in the log, but from before the start
CONFLICT = "A session for this runner already exists.\n"
NEW_SESSION = f"Runner reconnected.\n2026-10-26 04:03:25Z: {ci_runner.LISTENING}\n"


@pytest.fixture(autouse=True)
def _one_runner(monkeypatch):
    monkeypatch.setattr(settings, "ci_runners_per_project", 1)


def _docker(monkeypatch, logs_since_start: list[str]) -> tuple[list[list[str]], list[float]]:
    """A Docker with an older pair of the project; the runner's log since its start reads ``logs_since_start`` one
    after the other (the last one stays). Records the calls and the pauses."""
    calls: list[list[str]] = []
    pauses: list[float] = []
    reads = iter(logs_since_start)
    last = [""]

    def fake_run(cmd, **kwargs):
        cmd = list(cmd)
        calls.append(cmd)
        if cmd[1] == "ps":
            if "-a" in cmd and f"name=^{RUNNER}$" in cmd:
                return _Completed(f"{RUNNER}\tnex-ci-nex-demo\tan-older-template\n")
            return _Completed()
        if cmd[1] == "inspect":
            return _Completed(STARTED_AT + "\n")
        if cmd[1] == "logs":
            if cmd[cmd.index("--since") + 1] != STARTED_AT:
                return _Completed(OLD_SESSION + CONFLICT)  # the whole log — the old runner's line is in it
            last[0] = next(reads, last[0])
            return _Completed(last[0])
        return _Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(github_validation, "runner_registration_token", lambda repo_full, *, timeout: TOKEN)
    monkeypatch.setattr(ci_runner.time, "sleep", pauses.append)
    return calls, pauses


def _first(calls: list[list[str]], found) -> int:
    return next(i for i, cmd in enumerate(calls) if found(cmd))


def test_provision_returns_only_once_the_new_runner_listens(monkeypatch):
    calls, pauses = _docker(monkeypatch, [CONFLICT, CONFLICT, CONFLICT + NEW_SESSION])

    ci_runner.provision(SLUG, "rauschiccsk/nex-demo")

    reads = [cmd for cmd in calls if cmd[1] == "logs"]
    assert reads == [["docker", "logs", "--since", STARTED_AT, RUNNER]] * 3
    assert pauses == [ci_runner.SESSION_POLL] * 2
    started = _first(calls, lambda cmd: cmd[-3:] == ["up", "-d", "--wait"])
    assert started < _first(calls, lambda cmd: cmd[1] == "inspect") < _first(calls, lambda cmd: cmd[1] == "logs")


def test_a_runner_that_listens_since_its_start_holds_nothing(monkeypatch):
    calls, pauses = _docker(monkeypatch, [NEW_SESSION])

    ci_runner.provision(SLUG, "rauschiccsk/nex-demo")

    assert pauses == []
    assert [cmd for cmd in calls if cmd[1] == "logs"] == [["docker", "logs", "--since", STARTED_AT, RUNNER]]


def test_a_runner_without_a_session_of_its_own_is_named(monkeypatch):
    monkeypatch.setattr(settings, "ci_runner_session_wait_seconds", 0)
    _docker(monkeypatch, [CONFLICT])

    with pytest.raises(RuntimeError) as raised:
        ci_runner.provision(SLUG, "rauschiccsk/nex-demo")

    assert str(raised.value) == (
        f"CI runner {RUNNER} has no session of its own on GitHub 0 s after its start (docker logs {RUNNER})"
    )


def test_the_conversion_does_not_report_a_runner_without_a_session_converted(monkeypatch, capsys):
    monkeypatch.setattr(settings, "ci_runner_session_wait_seconds", 0)
    _docker(monkeypatch, [CONFLICT])
    monkeypatch.setattr(ci_runner, "github_busy", lambda repo_full, slug, *, timeout: False)
    monkeypatch.setattr(ci_runner, "github_status", lambda repo_full, slug, *, timeout: "online")  # GitHub says so

    code = ci_runners.main(["--apply", SLUG], projects=lambda: [(SLUG, "rauschiccsk/nex-demo")])

    out = capsys.readouterr().out
    assert code == 1
    assert f"{SLUG}: prevod zlyhal — CI runner {RUNNER} has no session of its own on GitHub" in out
    assert "prevedený" not in out

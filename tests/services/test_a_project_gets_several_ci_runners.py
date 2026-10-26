"""DEV-215 — a project gets several CI runners on ANDROS, so its jobs do not wait for the one there is.

23.10.2026 NEX Inbox's jobs waited for its only runner — lint 34 min, drift-check 33 min, test 31 min — while the agent
pushed after every task. Director: „Áno, súhlasím, založ všetkých päť tiketov.“, „Súhlasím“. Measured 24.10.2026: peak
load 20.5 of 80 cores in a week, never less than 163 GiB of memory free.

Pinned: the number is a setting (2); every runner is a whole pair of its own — daemon, work dir, compose project,
name on GitHub — numbered with an underscore no project name contains, all under the project's one label; founding
starts them all with one registration token, a project with the old single runner gets the second without losing
the first, one beyond the setting is removed, deleting the project removes them all; GitHub must report every one
online, and any one at work is busy.
"""

from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from backend.config.settings import Settings, settings
from backend.services import ci_runner, github_validation
from backend.services import create_project_postscaffold as mod
from backend.services import project_ci_runner as pcr

TOKEN = "AINVENTEDREGISTRATIONTOKEN02"
SLUG = "nex-demo"


@pytest.fixture(autouse=True)
def _two_runners(monkeypatch):
    monkeypatch.setattr(settings, "ci_runners_per_project", 2)


def test_every_project_gets_two_runners_unless_the_setting_says_otherwise():
    assert Settings.model_fields["ci_runners_per_project"].default == 2
    assert ci_runner.runners() == [1, 2]
    assert ci_runner.runner_names(SLUG) == ["andros-ubuntu-nex-demo", "andros-ubuntu-nex-demo_2"]


# ── the template ─────────────────────────────────────────────────────────────────────────────────────────────────


def _render(env: dict[str, str]) -> dict:
    """The template with its variables filled the way compose fills them (``${X}``, ``${X:-}``, ``${X:?…}``)."""

    def fill(m: re.Match) -> str:
        name, _op, _rest = m.group(1), m.group(2), m.group(3)
        return env.get(name, "")

    text = re.sub(r"\$\{([A-Z_]+)(?:(:-|:\?)([^}]*))?\}", fill, ci_runner.COMPOSE_FILE.read_text(encoding="utf-8"))
    return yaml.safe_load(text)


def _names(index: int) -> dict[str, str]:
    c = _render({"SLUG": SLUG, "RUNNER_SUFFIX": ci_runner.suffix(index), "REPO_URL": "r", "CI_RUNNER_TEMPLATE": "f"})
    runner, daemon = c["services"]["runner"], c["services"]["docker"]
    return {
        "compose": c["name"],
        "daemon": daemon["container_name"],
        "hostname": daemon["hostname"],
        "runner": runner["container_name"],
        "github": runner["environment"]["RUNNER_NAME"],
        "label": runner["environment"]["LABELS"],
        "workdir": runner["environment"]["RUNNER_WORKDIR"],
        "cleanup_checks": runner["environment"]["CI_DOCKER_NAME"],
    }


def test_the_first_runner_keeps_the_names_it_always_had():
    assert _names(1) == {
        "compose": "nex-ci-nex-demo",
        "daemon": "nex-ci-docker-nex-demo",
        "hostname": "nex-ci-docker-nex-demo",
        "runner": "nex-ci-runner-nex-demo",
        "github": "andros-ubuntu-nex-demo",
        "label": "andros-ubuntu-nex-demo",
        "workdir": "/opt/ci-work/nex-demo",
        "cleanup_checks": "nex-ci-docker-nex-demo",
    }


def test_the_second_runner_is_a_pair_of_its_own_under_the_projects_one_label():
    assert _names(2) == {
        "compose": "nex-ci-nex-demo_2",
        "daemon": "nex-ci-docker-nex-demo_2",
        "hostname": "nex-ci-docker-nex-demo_2",
        "runner": "nex-ci-runner-nex-demo_2",
        "github": "andros-ubuntu-nex-demo_2",
        "label": "andros-ubuntu-nex-demo",
        "workdir": "/opt/ci-work/nex-demo_2",
        "cleanup_checks": "nex-ci-docker-nex-demo_2",
    }


def test_the_second_runner_of_x_never_takes_a_name_of_the_first_of_x_2():
    """A project's name may end in a number („x-2“); it never contains an underscore."""
    second_of_x = {n for k, n in _names(2).items() if k != "label"}
    first_of_x_2 = set(
        _render({"SLUG": "nex-demo-2", "RUNNER_SUFFIX": "", "REPO_URL": "r", "CI_RUNNER_TEMPLATE": "f"})["services"][
            "runner"
        ]["environment"].values()
    )
    assert second_of_x.isdisjoint(first_of_x_2)


# ── founding, renewing, removing ─────────────────────────────────────────────────────────────────────────────────


class _Completed:
    def __init__(self, stdout: str = "") -> None:
        self.returncode, self.stdout, self.stderr = 0, stdout, ""


#: DEV-226: what a started runner answers when the cockpit waits for its session — it has one at once.
STARTED_AT = "2026-10-26T04:01:43.000000000Z"
#: The calls that only read Docker — left out of what was done.
READS = ("ps", "inspect", "logs")


def _read(cmd: list[str]) -> _Completed | None:
    """The start time and the log of a started runner, or None for a call that is not such a read."""
    if cmd[1] == "inspect":
        return _Completed(STARTED_AT + "\n")
    if cmd[1] == "logs":
        return _Completed(f"√ Connected to GitHub\n2026-10-26 04:02:00Z: {ci_runner.LISTENING}\n")
    return None


def _docker(monkeypatch, existing: dict[str, dict[str, str]]):
    """A Docker with the runner containers ``existing`` (name → labels); records every call and every token asked."""
    calls: list[tuple[list[str], dict]] = []
    tokens: list[str] = []

    def fake_run(cmd, **kwargs):
        cmd = list(cmd)
        calls.append((cmd, kwargs))
        if cmd[1] in ("inspect", "logs"):
            return _read(cmd)
        if cmd[1] != "ps":
            return _Completed()
        pattern = cmd[cmd.index("--filter") + 1].removeprefix("name=")
        fmt = cmd[cmd.index("--format") + 1]
        rows = []
        for name, labels in existing.items():
            if re.fullmatch(pattern.removeprefix("^").removesuffix("$"), name):
                row = fmt.replace("{{.Names}}", name)
                rows.append(re.sub(r'\{\{\.Label "([^"]+)"\}\}', lambda m: labels.get(m.group(1), ""), row))
        return _Completed("\n".join(rows) + ("\n" if rows else ""))

    def issue(repo_full, *, timeout):
        tokens.append(repo_full)
        return TOKEN

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(github_validation, "runner_registration_token", issue)
    return calls, tokens


def _current(index: int) -> dict[str, str]:
    return {
        "com.docker.compose.project": ci_runner.compose_project(SLUG, index),
        ci_runner.TEMPLATE_LABEL: ci_runner.template_fingerprint(),
    }


def _work(calls) -> list[str]:
    """What was done, as text: compose with the runner it was for, and whether a token went along."""
    out = []
    for cmd, kw in calls:
        if cmd[1] in READS:
            continue
        env = kw.get("env") or {}
        who = f"[{env['RUNNER_SUFFIX'] or '1'}] " if "RUNNER_SUFFIX" in env else ""
        token = " +token" if env.get("RUNNER_TOKEN") else ""
        out.append(who + " ".join(cmd).replace(str(ci_runner.COMPOSE_FILE), "TEMPLATE") + token)
    return out


def test_founding_starts_every_runner_with_one_registration_token(monkeypatch):
    calls, tokens = _docker(monkeypatch, {})

    ci_runner.provision(SLUG, "acme/nex-demo")

    assert _work(calls) == [
        "[1] docker compose --file TEMPLATE run --rm --no-TTY runner true +token",
        "[1] docker compose --file TEMPLATE up -d --wait",
        "[_2] docker compose --file TEMPLATE run --rm --no-TTY runner true +token",
        "[_2] docker compose --file TEMPLATE up -d --wait",
    ]
    assert tokens == ["acme/nex-demo"], "one registration token serves every runner"


def test_a_project_with_its_old_single_runner_gets_the_second_and_keeps_the_first(monkeypatch):
    calls, _tokens = _docker(monkeypatch, {"nex-ci-runner-nex-demo": _current(1)})

    assert ci_runner.state(SLUG) == ci_runner.OUTDATED, "one runner where two belong is not current"
    calls.clear()
    ci_runner.provision(SLUG, "acme/nex-demo")

    assert _work(calls) == [
        "[1] docker compose --file TEMPLATE up -d --wait",
        "[_2] docker compose --file TEMPLATE run --rm --no-TTY runner true +token",
        "[_2] docker compose --file TEMPLATE up -d --wait",
    ]


@pytest.mark.parametrize(
    ("existing", "state"),
    [
        ({}, ci_runner.NONE),
        ({"nex-ci-runner-nex-demo": _current(1), "nex-ci-runner-nex-demo_2": _current(2)}, ci_runner.CURRENT),
        ({"nex-ci-runner-nex-demo_2": _current(2)}, ci_runner.OUTDATED),
        ({"nex-ci-runner-nex-demo": {}, "nex-ci-runner-nex-demo_2": _current(2)}, ci_runner.LEGACY),
        (
            {
                "nex-ci-runner-nex-demo": _current(1),
                "nex-ci-runner-nex-demo_2": _current(2),
                "nex-ci-runner-nex-demo_3": _current(3),
            },
            ci_runner.OUTDATED,
        ),
    ],
    ids=["none", "both-current", "first-missing", "first-on-the-live-docker", "one-too-many"],
)
def test_the_state_of_a_project_counts_every_runner(monkeypatch, existing, state):
    _docker(monkeypatch, existing)

    assert ci_runner.state(SLUG) == state


def test_a_runner_beyond_the_setting_is_removed(monkeypatch):
    calls, _tokens = _docker(
        monkeypatch,
        {
            "nex-ci-runner-nex-demo": _current(1),
            "nex-ci-runner-nex-demo_2": _current(2),
            "nex-ci-runner-nex-demo_3": _current(3),
        },
    )

    ci_runner.provision(SLUG, "acme/nex-demo")

    assert _work(calls)[-1] == "docker compose --project-name nex-ci-nex-demo_3 down --volumes"


def test_deleting_a_project_removes_every_runner_it_has(monkeypatch):
    calls, _tokens = _docker(
        monkeypatch, {"nex-ci-runner-nex-demo": _current(1), "nex-ci-runner-nex-demo_3": _current(3)}
    )

    ci_runner.deprovision(SLUG)

    assert _work(calls) == [
        "docker compose --project-name nex-ci-nex-demo down --volumes",
        "docker compose --project-name nex-ci-nex-demo_2 down --volumes",
        "docker compose --project-name nex-ci-nex-demo_3 down --volumes",
    ]


# ── what GitHub says ─────────────────────────────────────────────────────────────────────────────────────────────


def _github(monkeypatch, runners: dict[str, dict]):
    asked: list[str] = []

    def status(repo_full, name, *, timeout):
        asked.append(name)
        return (runners.get(name) or {}).get("status")

    def busy(repo_full, name, *, timeout):
        return bool((runners.get(name) or {}).get("busy"))

    monkeypatch.setattr(github_validation, "runner_status", status)
    monkeypatch.setattr(github_validation, "runner_busy", busy)
    return asked


@pytest.mark.parametrize(
    ("runners", "status"),
    [
        ({"andros-ubuntu-nex-demo": {"status": "online"}, "andros-ubuntu-nex-demo_2": {"status": "online"}}, "online"),
        (
            {"andros-ubuntu-nex-demo": {"status": "online"}, "andros-ubuntu-nex-demo_2": {"status": "offline"}},
            "offline",
        ),
        ({"andros-ubuntu-nex-demo": {"status": "online"}}, None),
    ],
    ids=["every-one-online", "one-offline", "one-not-registered"],
)
def test_the_project_is_online_only_when_every_runner_is(monkeypatch, runners, status):
    asked = _github(monkeypatch, runners)

    assert ci_runner.github_status("acme/nex-demo", SLUG, timeout=5) == status
    assert asked == ["andros-ubuntu-nex-demo", "andros-ubuntu-nex-demo_2"]


def test_any_runner_at_work_makes_the_project_busy(monkeypatch):
    _github(monkeypatch, {"andros-ubuntu-nex-demo_2": {"status": "online", "busy": True}})

    assert ci_runner.github_busy("acme/nex-demo", SLUG, timeout=5) is True


def test_the_tab_says_how_many_runners_the_checks_run_on():
    summary, action = pcr.decide(
        label="andros-ubuntu-nex-demo",
        repo="acme/nex-demo",
        archived=False,
        state=ci_runner.CURRENT,
        github_status="online",
        github_error=False,
        starting=False,
    )

    assert (summary, action) == (
        "Kontroly bežia na 2 vlastných vykonávačoch andros-ubuntu-nex-demo na ANDROSe, prihlásených na GitHube.",
        None,
    )


def test_founding_waits_until_every_runner_listens(monkeypatch):
    _github(monkeypatch, {"andros-ubuntu-nex-demo": {"status": "online"}})

    said = mod._wait_for_ci_runner(SLUG, "acme/nex-demo", wait=0, poll=0)

    assert said == mod.CI_RUNNER_NEVER_ONLINE.format(wait=0), "the second runner never came online"

"""DEV-145 — a project's CI runner works against a Docker daemon of its own, never the server's live one.

Until 19.10.2026 founding started ``nex-ci-runner-<slug>`` with ``/var/run/docker.sock`` mounted in: a CI job of any
project could see, stop and delete the customers' containers and databases on ANDROS, and whatever a cancelled job
left running stayed there (15.10.2026 a NEX Manager test database ran for four days, open on the server's network).
The runner also kept the cockpit's GitHub key in its environment for good, readable by any job through that socket.

Pinned here: the template (``templates/ci-runner/``) gives the runner only its own daemon and keeps what the old
runners got right; the cleanup hook empties only that daemon; founding, conversion and removal go through it, and the
registration token reaches nothing but a one-off container.
"""

from __future__ import annotations

import configparser
import hashlib
import os
import re
import subprocess
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import yaml

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.scripts import ci_runners
from backend.services import ci_runner, github_validation
from backend.services import create_project_postscaffold as mod
from backend.services import system_setting as system_setting_service

REGISTRATION_TOKEN = "AINVENTEDREGISTRATIONTOKEN01"
LIVE_DOCKER = "nex-andros-server"  # what ``docker info .Name`` says about ANDROS's live Docker


@pytest.fixture(scope="module")
def template() -> dict:
    return yaml.safe_load(ci_runner.COMPOSE_FILE.read_text(encoding="utf-8"))


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _issue_token(repo_full: str, *, timeout: float) -> str:
    return REGISTRATION_TOKEN


#: A runner started with ``docker run`` carries no labels at all.
OLD_WAY: dict[str, str] = {}


def _pair(fingerprint: str) -> dict[str, str]:
    return {"com.docker.compose.project": "nex-ci-nex-demo", ci_runner.TEMPLATE_LABEL: fingerprint}


def _docker_ps(cmd: list[str], labels: dict[str, str] | None) -> str:
    """What ``docker ps --format`` prints for a runner container with *labels* (None = no such container), rendered
    from the format the code sends — so the row is the one Docker would give that code, not one the test made up."""
    if labels is None:
        return ""
    name = cmd[cmd.index("--filter") + 1].removeprefix("name=^").removesuffix("$")
    row = cmd[cmd.index("--format") + 1].replace("{{.Names}}", name)
    return re.sub(r'\{\{\.Label "([^"]+)"\}\}', lambda m: labels.get(m.group(1), ""), row) + "\n"


def _record_docker(
    monkeypatch: pytest.MonkeyPatch, *, existing: dict[str, str] | None = None
) -> list[tuple[list[str], dict]]:
    """Every ``docker`` call made; ``docker ps`` finds a runner container with the labels *existing* (None = none)."""
    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return _Completed(stdout=_docker_ps(list(cmd), existing) if "ps" in cmd else "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(github_validation, "runner_registration_token", _issue_token)
    return calls


def _compose_calls(calls: list[tuple[list[str], dict]]) -> list[str]:
    """The compose and rm calls as text, with whether a registration token was in the environment."""
    template = str(ci_runner.COMPOSE_FILE)
    return [
        f"token={'yes' if (kw.get('env') or {}).get('RUNNER_TOKEN') else 'no'} "
        + " ".join(cmd).replace(template, "TEMPLATE")
        for cmd, kw in calls
        if cmd[1] != "ps"
    ]


# --- the template ----------------------------------------------------------------------------------------------------


def test_the_runner_drives_its_own_daemon_and_never_the_live_one(template: dict) -> None:
    runner, daemon = template["services"]["runner"], template["services"]["docker"]
    every_mount = [v.split(":")[0] for service in template["services"].values() for v in service.get("volumes", [])]
    assert [m for m in every_mount if "docker.sock" in m] == [], "no service may get a host Docker socket"
    socket = runner["environment"]["DOCKER_HOST"].removeprefix("unix://")
    assert f"--host=unix://{socket}" in daemon["command"]
    socket_dir = str(Path(socket).parent)
    shared = {v.split(":")[0] for v in runner["volumes"] if v.split(":")[1] == socket_dir}
    assert shared and shared == {v.split(":")[0] for v in daemon["volumes"] if v.split(":")[1] == socket_dir}
    assert [c for c in daemon["command"] if c.startswith("--host=tcp")] == [], "the own daemon has no network access"


def test_a_port_a_test_opens_is_on_the_runners_localhost_and_not_on_the_servers_network(template: dict) -> None:
    services = template["services"]
    assert services["runner"]["network_mode"] == "service:docker"
    assert "ports" not in services["docker"] and "ports" not in services["runner"]


def test_ci_networks_do_not_overlap_the_office(template: dict) -> None:
    command = template["services"]["docker"]["command"]
    assert "--bip=10.230.0.1/24" in command and "--default-address-pool=base=10.231.0.0/16,size=24" in command


def test_the_work_dir_is_the_same_path_in_the_runner_and_its_daemon(template: dict) -> None:
    """ICCINT-66: a bind mount in a CI compose file is resolved by the daemon that starts the container. With the
    checkout elsewhere for the daemon, Docker silently mounts an EMPTY directory — nex-productcatalogs lost a day to a
    database whose init script "did not exist"."""
    runner, daemon = template["services"]["runner"], template["services"]["docker"]
    workdir = runner["environment"]["RUNNER_WORKDIR"]
    assert workdir == "/opt/ci-work/${SLUG}"
    assert f"{workdir}:{workdir}" in runner["volumes"] and f"{workdir}:{workdir}" in daemon["volumes"]


def test_the_work_dir_is_handed_to_the_runner_user_before_the_runner_starts(template: dict) -> None:
    """A runner started the old way ran as root and left root-owned files in the work dir; the image's own start takes
    over only its top. On 19.10.2026 the converted runners of nex-websites and career-asistent failed their first job
    on them ("Access to the path …/PipelineFolder.json is denied")."""
    workdir, runner = template["services"]["workdir"], template["services"]["runner"]
    path = runner["environment"]["RUNNER_WORKDIR"]
    assert workdir["image"] == runner["image"], "the runner image is the one that knows user runner"
    assert workdir["user"] == "root"
    assert workdir["entrypoint"] == ["chown", "-R", "runner", path]
    assert workdir["volumes"] == [f"{path}:{path}"] and workdir["network_mode"] == "none"
    assert runner["depends_on"]["workdir"] == {"condition": "service_completed_successfully"}


def test_the_runner_survives_its_own_update(template: dict) -> None:
    """ICCINT-165: with the image's default command an update ends the container, Docker restarts it halfway through
    the swap of ``bin`` and the runner is left without it (03.10.2026) or on a version GitHub no longer serves
    (19.10.2026). ``run.sh`` relaunches the listener in the same container. And the runner must be allowed to update:
    on 24.09.2026 runners that were not went into a nine-day restart loop."""
    runner = template["services"]["runner"]
    assert runner["command"] == ["./run.sh"]
    assert "DISABLE_AUTO_UPDATE" not in runner["environment"]


def test_the_runner_reads_like_the_old_runners_on_the_server(template: dict) -> None:
    """19.10.2026 eight cockpit tests failed on a runner that ran as root on Ubuntu 20.04 (git 2.50) without a git
    identity; the old runners were user andros on the server (Ubuntu 24.04, git 2.43) with identity ICC CI."""
    runner, daemon = template["services"]["runner"], template["services"]["docker"]
    assert runner["image"].startswith("myoung34/github-runner:") and runner["image"].endswith("-ubuntu-noble")
    assert runner["environment"]["RUN_AS_ROOT"] == "false"
    assert "--group=500" in daemon["command"], "user runner's docker group (GID 500) must own the socket"
    assert "--feature=containerd-snapshotter=false" in daemon["command"], "images stored like on the server (overlay2)"
    identity_at = {c["source"]: c["target"] for c in runner["configs"]}["git-identity"]
    assert identity_at == "/home/runner/.gitconfig"
    identity = configparser.ConfigParser()
    identity.read_string(template["configs"]["git-identity"]["content"])
    assert identity["user"]["name"] == "ICC CI" and identity["user"]["email"] == "ci@icc.sk"


def test_no_key_stays_in_the_runners_configuration(template: dict) -> None:
    env = template["services"]["runner"]["environment"]
    assert env["RUNNER_TOKEN"] is None, "the token comes only from the environment of the one-off registration"
    assert "ACCESS_TOKEN" not in env
    assert env["DISABLE_AUTOMATIC_DEREGISTRATION"] == "true" and env["UNSET_CONFIG_VARS"] == "true"
    assert f"runner-data:{env['CONFIGURED_ACTIONS_RUNNER_FILES_DIR']}" in template["services"]["runner"]["volumes"]


def test_the_cleanup_hook_runs_at_both_ends_of_a_job_against_the_daemon_by_name(template: dict) -> None:
    runner, daemon = template["services"]["runner"], template["services"]["docker"]
    env = runner["environment"]
    hook_at = {c["source"]: c["target"] for c in runner["configs"]}["cleanup-hook"]
    assert env["ACTIONS_RUNNER_HOOK_JOB_STARTED"] == env["ACTIONS_RUNNER_HOOK_JOB_COMPLETED"] == hook_at
    assert template["configs"]["cleanup-hook"] == {"environment": "CI_CLEANUP_HOOK"}
    assert env["CI_DOCKER_NAME"] == daemon["hostname"] != LIVE_DOCKER


def test_nothing_is_mounted_from_a_file_next_to_the_template(template: dict) -> None:
    """The cockpit runs compose inside its own container: a path next to the template is not on the host, and the
    host's Docker would mount an empty directory in its place. Every bind source is an absolute host path."""
    sources = [v.split(":")[0] for service in template["services"].values() for v in service.get("volumes", [])]
    binds = [s for s in sources if s not in template["volumes"]]
    assert binds and all(s.startswith("/opt/ci-work/") for s in binds), binds
    assert all(set(c) == {"content"} or set(c) == {"environment"} for c in template["configs"].values())


def test_the_runner_carries_the_fingerprint_of_its_template(template: dict) -> None:
    label = template["services"]["runner"]["labels"][ci_runner.TEMPLATE_LABEL]
    assert label == "${CI_RUNNER_TEMPLATE:?CI_RUNNER_TEMPLATE is required}"
    expected = hashlib.sha256(ci_runner.COMPOSE_FILE.read_bytes() + ci_runner.CLEANUP_HOOK.read_bytes()).hexdigest()
    assert ci_runner.template_fingerprint() == expected


# --- the cleanup hook ------------------------------------------------------------------------------------------------


def _run_hook(tmp_path: Path, name: str | None, containers: str = "", expected: str | None = "nex-ci-docker-x"):
    """cleanup.sh with a stand-in ``docker`` that records its calls and answers ``info`` with *name* (None = silent)."""
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    record = tmp_path / "calls.txt"
    (stub / "docker").write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{record}"\n'
        'case "$1" in\n'
        f"  info) {'exit 1' if name is None else f'echo {name}'} ;;\n"
        f"  ps) printf '%s' '{containers}' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (stub / "docker").chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("CI_DOCKER_NAME", "DOCKER_HOST")}
    env["PATH"] = f"{stub}:{env['PATH']}"
    if expected is not None:
        env["CI_DOCKER_NAME"] = expected
    run = subprocess.run(["bash", str(ci_runner.CLEANUP_HOOK)], env=env, capture_output=True, text=True, timeout=30)
    calls = record.read_text(encoding="utf-8").splitlines() if record.exists() else []
    return run, calls


def test_the_hook_empties_the_runners_own_daemon(tmp_path: Path) -> None:
    run, calls = _run_hook(tmp_path, "nex-ci-docker-x", "a1\nb2\n")
    assert run.returncode == 0, run.stderr
    assert calls == [
        "info --format {{.Name}}",
        "ps -aq",
        "rm -f a1 b2",
        "volume prune -af",
        "network prune -f",
        "image prune -f",
    ]
    assert run.stdout.strip() == "cleanup.sh: own Docker nex-ci-docker-x emptied (containers: 2)"


def test_the_hook_without_containers_removes_none(tmp_path: Path) -> None:
    run, calls = _run_hook(tmp_path, "nex-ci-docker-x")
    assert run.returncode == 0, run.stderr
    assert calls == ["info --format {{.Name}}", "ps -aq", "volume prune -af", "network prune -f", "image prune -f"]


@pytest.mark.parametrize(
    ("name", "expected", "message"),
    [
        (
            LIVE_DOCKER,
            "nex-ci-docker-x",
            f"this Docker is called '{LIVE_DOCKER}', not 'nex-ci-docker-x' — not the runner's own, emptying nothing",
        ),
        (None, "nex-ci-docker-x", "the runner's own Docker does not answer (DOCKER_HOST is missing)"),
        ("nex-ci-docker-x", None, "CI_DOCKER_NAME is missing — emptying nothing"),
        ("nex-ci-docker-x", "", "CI_DOCKER_NAME is missing — emptying nothing"),
    ],
    ids=["live-docker", "docker-silent", "name-missing", "name-empty"],
)
def test_the_hook_handed_another_daemon_touches_nothing(tmp_path: Path, name, expected, message) -> None:
    """Safeguard: ANDROS's live Docker has another name — the hook exits 1 and removes, stops or prunes nothing."""
    run, calls = _run_hook(tmp_path, name, "a1\n", expected=expected)
    assert run.returncode == 1
    assert run.stderr.splitlines() == [f"cleanup.sh: {message}"]
    assert [c for c in calls if not c.startswith("info")] == []


# --- founding, conversion, removal -----------------------------------------------------------------------------------


def test_founding_never_hands_a_runner_the_live_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_docker(monkeypatch)

    mod._provision_ci_runner("nex-demo", "rauschiccsk/nex-demo")

    assert calls, "founding started no runner at all"
    handed = [cmd for cmd, _ in calls if any("docker.sock" in str(arg) for arg in cmd)]
    assert handed == [], "a runner must never get the server's Docker socket"


def test_founding_registers_once_and_only_a_one_off_container_gets_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runner itself starts without a key: on 19.10.2026 a runner started with the key and at once again without
    it made GitHub hold the old session for three minutes. A key in the runner's environment is readable by a job."""
    calls = _record_docker(monkeypatch)

    mod._provision_ci_runner("nex-demo", "rauschiccsk/nex-demo")

    assert _compose_calls(calls) == [
        "token=yes docker compose --file TEMPLATE run --rm --no-TTY runner true",
        "token=no docker compose --file TEMPLATE up -d --wait",
    ]
    assert all(REGISTRATION_TOKEN not in " ".join(cmd) for cmd, _ in calls), "the token never goes on argv"


@pytest.mark.parametrize(
    ("repo_url", "github_org"),
    [("acme-org/nex-demo", None), ("https://github.com/acme-org/nex-demo", None), (None, "acme-org")],
)
def test_compose_gets_the_project_the_hook_and_the_fingerprint(
    monkeypatch: pytest.MonkeyPatch, repo_url: str | None, github_org: str | None
) -> None:
    """The runner registers against the project's REAL owner — read from ``repo_url`` in either stored shape, else the
    configured organisation. A runner registered against another repository leaves every CI job queued forever."""
    monkeypatch.setenv("RUNNER_TOKEN", "a-stale-token-from-somewhere")
    calls = _record_docker(monkeypatch)

    mod._provision_ci_runner("nex-demo", repo_url, github_org=github_org)

    env = calls[-1][1]["env"]
    assert env["SLUG"] == "nex-demo"
    assert env["REPO_URL"] == "https://github.com/acme-org/nex-demo"
    assert env["CI_CLEANUP_HOOK"] == ci_runner.CLEANUP_HOOK.read_text(encoding="utf-8")
    assert env["CI_RUNNER_TEMPLATE"] == ci_runner.template_fingerprint()
    assert "RUNNER_TOKEN" not in env, "the runner must start without any token"


def test_a_runner_started_the_old_way_is_replaced_by_the_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_docker(monkeypatch, existing=OLD_WAY)

    ci_runner.provision("nex-demo", "rauschiccsk/nex-demo")

    assert _compose_calls(calls) == [
        "token=no docker rm -f -v nex-ci-runner-nex-demo",
        "token=yes docker compose --file TEMPLATE run --rm --no-TTY runner true",
        "token=no docker compose --file TEMPLATE up -d --wait",
    ]


def test_a_pair_from_an_older_template_is_recreated_and_keeps_its_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_docker(monkeypatch, existing=_pair("an-older-fingerprint"))

    ci_runner.provision("nex-demo", "rauschiccsk/nex-demo")

    assert _compose_calls(calls) == [
        "token=yes docker compose --file TEMPLATE run --rm --no-TTY runner true",
        "token=no docker compose --file TEMPLATE up -d --wait",
    ]


def test_a_current_pair_is_only_started_and_github_is_not_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_docker(monkeypatch, existing=_pair(ci_runner.template_fingerprint()))
    monkeypatch.setattr(github_validation, "runner_registration_token", lambda *a, **k: pytest.fail("token asked"))

    ci_runner.provision("nex-demo", "rauschiccsk/nex-demo")

    assert _compose_calls(calls) == ["token=no docker compose --file TEMPLATE up -d --wait"]


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        (None, ci_runner.NONE),
        (OLD_WAY, ci_runner.LEGACY),
        ({"com.docker.compose.project": "nex-ci-nex-other", ci_runner.TEMPLATE_LABEL: "x"}, ci_runner.LEGACY),
        (_pair("an-older-fingerprint"), ci_runner.OUTDATED),
        ({"com.docker.compose.project": "nex-ci-nex-demo"}, ci_runner.OUTDATED),
    ],
)
def test_state_names_the_runner_the_project_has(monkeypatch: pytest.MonkeyPatch, existing, expected) -> None:
    calls = _record_docker(monkeypatch, existing=existing)

    assert ci_runner.state("nex-demo") == expected
    assert calls[0][0][:5] == ["docker", "ps", "-a", "--filter", "name=^nex-ci-runner-nex-demo$"]


def test_state_of_a_current_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    _record_docker(monkeypatch, existing=_pair(ci_runner.template_fingerprint()))

    assert ci_runner.state("nex-demo") == ci_runner.CURRENT


def test_a_runner_that_cannot_be_started_raises_without_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, **kwargs):
        if "ps" in cmd:
            return _Completed()
        return _Completed(returncode=1, stderr="Error response from daemon: pull access denied")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(github_validation, "runner_registration_token", _issue_token)

    with pytest.raises(RuntimeError) as raised:
        ci_runner.provision("nex-demo", "rauschiccsk/nex-demo")
    assert "pull access denied" in str(raised.value)
    assert REGISTRATION_TOKEN not in str(raised.value)


def test_deleting_a_project_removes_its_pair_with_volumes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_docker(monkeypatch, existing=_pair(ci_runner.template_fingerprint()))

    ci_runner.deprovision("nex-demo")

    assert _compose_calls(calls) == ["token=no docker compose --project-name nex-ci-nex-demo down --volumes"]
    assert calls[-1][1]["cwd"] == "/"


def test_deleting_a_project_removes_a_runner_started_the_old_way(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_docker(monkeypatch, existing=OLD_WAY)

    ci_runner.deprovision("nex-demo")

    assert _compose_calls(calls) == [
        "token=no docker rm -f -v nex-ci-runner-nex-demo",
        "token=no docker compose --project-name nex-ci-nex-demo down --volumes",
    ]


def test_deleting_a_project_never_raises_over_its_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("docker")))

    ci_runner.deprovision("nex-demo")


# --- what the cockpit asks GitHub -------------------------------------------------------------------------------------


def _github(status_code: int, body: dict) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = body
    response.text = str(body)
    return response


def test_a_registration_token_is_asked_for_the_projects_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def _post(url, headers, timeout):
        seen["url"] = url
        return _github(201, {"token": REGISTRATION_TOKEN, "expires_at": "2026-10-19T16:00:00Z"})

    monkeypatch.setattr(github_validation.httpx, "post", _post)

    assert github_validation.runner_registration_token("acme-org/nex-demo", timeout=5) == REGISTRATION_TOKEN
    assert (
        seen["url"] == f"{github_validation.GITHUB_API_BASE}/repos/acme-org/nex-demo/actions/runners/registration-token"
    )


def test_a_refused_registration_token_raises_without_what_github_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"message": "Resource not accessible", "token": REGISTRATION_TOKEN}
    monkeypatch.setattr(github_validation.httpx, "post", lambda url, headers, timeout: _github(403, body))

    with pytest.raises(RuntimeError) as raised:
        github_validation.runner_registration_token("acme-org/nex-demo", timeout=5)
    assert "403" in str(raised.value) and REGISTRATION_TOKEN not in str(raised.value)


@pytest.mark.parametrize(
    ("runners", "busy"),
    [
        ([{"name": "andros-ubuntu-nex-demo", "status": "online", "busy": True}], True),
        ([{"name": "andros-ubuntu-nex-demo", "status": "online", "busy": False}], False),
        ([{"name": "andros-ubuntu-other", "status": "online", "busy": True}], False),
        ([], False),
    ],
    ids=["working", "idle", "another-runner-working", "not-registered"],
)
def test_runner_busy_says_whether_the_named_runner_runs_a_job(monkeypatch: pytest.MonkeyPatch, runners, busy) -> None:
    body = {"total_count": len(runners), "runners": runners}
    monkeypatch.setattr(github_validation.httpx, "get", lambda url, headers, timeout: _github(200, body))

    assert github_validation.runner_busy("acme-org/nex-demo", "andros-ubuntu-nex-demo", timeout=5) is busy


# --- converting the runners the projects already have ----------------------------------------------------------------


PROJECTS = [
    ("career-asistent", "rauschiccsk/career-asistent"),
    ("nex-inbox", "rauschiccsk/nex-inbox"),
    ("nex-manager", "rauschiccsk/nex-manager"),
    ("nex-websites", "rauschiccsk/nex-websites"),
]
STATES = {
    "career-asistent": ci_runner.LEGACY,
    "nex-inbox": ci_runner.NONE,
    "nex-manager": ci_runner.OUTDATED,
    "nex-websites": ci_runner.LEGACY,
}


def _convert(monkeypatch: pytest.MonkeyPatch, capsys, argv: list[str], *, busy=(), broken=()):
    states = dict(STATES)
    converted: list[str] = []

    def provision(slug, repo_full):
        if slug in broken:
            raise RuntimeError("docker compose up failed: pull access denied")
        converted.append(f"{slug} {repo_full}")
        states[slug] = ci_runner.CURRENT

    monkeypatch.setattr(ci_runner, "state", lambda slug: states[slug])
    monkeypatch.setattr(ci_runner, "provision", provision)
    monkeypatch.setattr(github_validation, "runner_busy", lambda repo_full, label, *, timeout: label in busy)
    monkeypatch.setattr(github_validation, "runner_status", lambda repo_full, label, *, timeout: "online")
    code = ci_runners.main(argv, projects=lambda: PROJECTS)
    return code, converted, capsys.readouterr().out.splitlines()


def test_without_apply_it_only_says_which_runner_each_project_has(monkeypatch, capsys) -> None:
    code, converted, out = _convert(monkeypatch, capsys, [])

    assert code == 0 and converted == []
    assert out == [
        "career-asistent: bežec s ostrým Dockerom servera — previesť",
        "nex-inbox: bez bežca (CI beží inde alebo ho projekt nemá)",
        "nex-manager: bežec s vlastným Dockerom zo staršieho predpisu — previesť",
        "nex-websites: bežec s ostrým Dockerom servera — previesť",
        "NANEČISTO — nič sa nezmenilo; previesť treba 3. Prevod: --apply [projekt …]",
    ]


def test_apply_converts_only_the_projects_named(monkeypatch, capsys) -> None:
    code, converted, out = _convert(monkeypatch, capsys, ["--apply", "nex-websites", "nex-inbox"])

    assert code == 0
    assert converted == ["nex-websites rauschiccsk/nex-websites"], "a project without a runner gets none"
    assert out[-1] == "nex-websites: prevedený — bežec s vlastným Dockerom, aktuálny; na GitHube online"


def test_apply_without_names_converts_every_runner_that_is_not_current(monkeypatch, capsys) -> None:
    code, converted, _ = _convert(monkeypatch, capsys, ["--apply"])

    assert code == 0
    assert [c.split()[0] for c in converted] == ["career-asistent", "nex-manager", "nex-websites"]


def test_a_runner_running_a_job_is_left_alone(monkeypatch, capsys) -> None:
    code, converted, out = _convert(monkeypatch, capsys, ["--apply"], busy={"andros-ubuntu-nex-manager"})

    assert code == 1
    assert [c.split()[0] for c in converted] == ["career-asistent", "nex-websites"]
    assert "nex-manager: práve robí úlohu CI — nechaný tak; prevod spusti znovu po jej skončení" in out


def test_one_failed_conversion_does_not_stop_the_others(monkeypatch, capsys) -> None:
    code, converted, out = _convert(monkeypatch, capsys, ["--apply"], broken={"career-asistent"})

    assert code == 1
    assert [c.split()[0] for c in converted] == ["nex-manager", "nex-websites"]
    assert "career-asistent: prevod zlyhal — docker compose up failed: pull access denied" in out


def test_an_unknown_project_is_refused_before_anything_changes(monkeypatch, capsys) -> None:
    code, converted, out = _convert(monkeypatch, capsys, ["--apply", "nex-webistes"])

    assert code == 2 and converted == []
    assert out == ["kokpit taký projekt nemá: nex-webistes"]


def test_the_projects_come_with_the_repository_their_runner_registers_against(db_session) -> None:
    system_setting_service.upsert(db_session, "github_org", "acme-org")
    user = User(
        username=f"ci-{uuid.uuid4().hex[:8]}",
        email=f"ci-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    for slug, repo_url in (("zz-ci-short", "other-org/zz-ci-short"), ("zz-ci-none", None)):
        db_session.add(
            Project(
                name=slug,
                slug=slug,
                type="standard",
                auth_mode="password",
                description="DEV-145 probe",
                created_by=user.id,
                repo_url=repo_url,
            )
        )
    db_session.flush()

    found = dict(ci_runners.projects_in(db_session))

    assert found["zz-ci-short"] == "other-org/zz-ci-short"
    assert found["zz-ci-none"] == "acme-org/zz-ci-none", "without a repository the configured organisation"

"""DEV-167 — the CI runners' own Docker holds no rights on the server; a privileged one only for the job that asks.

Measured 25.10.2026 on ANDROS: the Docker of every runner (14 of 14) ran privileged and had the server's disks in it —
512 B read from both nvme0n1 and sda, where the customers' installations live — and every step of every job could
start a privileged container in it (NEX Inbox's smb-blackhole does). A step that installs a foreign package could have
read and written the whole server. The one job that needs full rights is NEX Inbox's smb-blackhole. Director: „Áno,
súhlasím, začni DEV-167“.

Pinned: the daemon is rootless, not privileged, with no device of the server but the network's tun, runs as the
runner's own user under the AppArmor profile that names itself and nothing else; the privileged runner is the old
Docker under a label of its own, started for a project exactly while one of its workflows names that label, counted
in its state and on GitHub, and removed with the project.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from backend.config.settings import settings
from backend.services import ci_runner
from tests.services.test_a_project_gets_several_ci_runners import READS, _docker

SLUG = "nex-demo"


@pytest.fixture(autouse=True)
def _one_runner(monkeypatch):
    monkeypatch.setattr(settings, "ci_runners_per_project", 1)


@pytest.fixture()
def projects(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(ci_runner, "PROJECTS_ROOT", tmp_path)
    return tmp_path


def _workflow(projects: Path, slug: str, runs_on: str) -> None:
    wf = projects / slug / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "ci.yml").write_text(f"jobs:\n  smb-blackhole:\n    runs-on: {runs_on}\n", encoding="utf-8")


def _rendered(*files: Path, suffix: str = "") -> dict:
    """The template as ``docker compose`` lays the files over each other and fills them."""
    env = {"SLUG": SLUG, "RUNNER_SUFFIX": suffix, "REPO_URL": "https://github.com/a/b", "CI_CLEANUP_HOOK": "x"}
    env["CI_RUNNER_TEMPLATE"] = "f"
    args = [a for f in files for a in ("--file", str(f))]
    out = subprocess.run(
        ["docker", "compose", *args, "config"], capture_output=True, text=True, env=env, check=True, timeout=60
    )
    return yaml.safe_load(out.stdout)


def _raw() -> dict:
    return yaml.safe_load(ci_runner.COMPOSE_FILE.read_text(encoding="utf-8"))


# ── the rootless daemon ──────────────────────────────────────────────────────────────────────────────────────────


def test_the_daemon_is_rootless_and_not_privileged():
    daemon = _raw()["services"]["docker"]

    assert daemon["image"].startswith("docker:") and daemon["image"].endswith("-dind-rootless")
    assert not daemon.get("privileged"), "a privileged daemon has the server's disks in it"
    assert daemon["devices"] == ["/dev/net/tun"], "no device of the server but the network's tun"
    assert not daemon.get("cap_add") and "/var/run/docker.sock" not in str(daemon)


#: The image's user, the one rootlesskit maps the namespace's 0 to (docker:*-dind-rootless).
DAEMON_USER, DAEMON_ID = "rootless", 1000
#: What a job's containers use toward the runner: its user, its group and the socket's group (runner image).
RUNNER_UID, RUNNER_GIDS = 1001, (121, 500)


def _namespace(content: str) -> dict[int, int]:
    """The namespace id → server id map rootlesskit builds: 0 → the daemon's own, then the ranges in file order."""
    mapping, inside = {0: DAEMON_ID}, 1
    for line in content.splitlines():
        owner, start, count = line.split(":")
        assert owner == DAEMON_USER, line
        for offset in range(int(count)):
            mapping[inside] = int(start) + offset
            inside += 1
    return mapping


def test_the_runners_ids_pass_through_the_namespace_unshifted():
    """A tool container run as the runner (NEX Inbox: -u 1001:121 --group-add 500) reaches the socket and leaves files
    the runner removes. Measured 25.10.2026 with every id shifted: NEX Inbox's lint stopped at "setgroups: invalid
    argument" — the socket's group was 100120 from the runner's side."""
    template = _raw()
    daemon, configs = template["services"]["docker"], template["configs"]
    uids = _namespace(configs["rootless-subuid"]["content"])
    gids = _namespace(configs["rootless-subgid"]["content"])

    assert "user" not in daemon, "the image's own user runs the daemon"
    assert len(uids) == len(gids) == 65537, "a full namespace — 0 and 65536 ids"
    assert uids[RUNNER_UID] == RUNNER_UID
    assert all(gids[g] == g for g in RUNNER_GIDS)
    assert "--group=500" in daemon["command"]
    passing = {0, RUNNER_UID, *RUNNER_GIDS}
    assert all(server >= 100000 for inside, server in uids.items() if inside not in passing)
    assert all(server >= 100000 for inside, server in gids.items() if inside not in passing)
    assert len(set(uids.values())) == len(uids) and len(set(gids.values())) == len(gids), "no server id twice"
    assert {c["target"]: c["source"] for c in daemon["configs"]} == {
        "/etc/subuid": "rootless-subuid",
        "/etc/subgid": "rootless-subgid",
    }


def test_the_daemon_names_its_dns_servers():
    """Rootless, containers and image builds would ask vpnkit's DNS proxy, which answers a lookup of IPv4 and IPv6 at
    once with nothing — NEX Inbox's `npm ci` in its image build: "getaddrinfo ENOTFOUND codeload.github.com"
    (25.10.2026). The daemon names the servers the old privileged one used."""
    command = _raw()["services"]["docker"]["command"]

    assert [c for c in command if c.startswith("--dns")] == ["--dns=8.8.8.8", "--dns=8.8.4.4"]


def test_the_apparmor_profile_names_itself_and_nothing_else():
    """Ubuntu 24.04 lets no unprivileged process create a user namespace unless its profile says so. A profile with an
    attachment path would say so for every process that runs that path; this one only for a container naming it."""
    daemon = _raw()["services"]["docker"]
    asked = [o.removeprefix("apparmor=") for o in daemon["security_opt"] if o.startswith("apparmor=")]
    profile = (ci_runner.TEMPLATE_DIR / "nex-ci-dind-rootless.apparmor").read_text(encoding="utf-8")
    declared = re.findall(r"^profile\s+(\S+)\s+(.*)\{\s*$", profile, re.M)

    assert asked == ["nex-ci-dind-rootless"]
    assert declared == [("nex-ci-dind-rootless", "flags=(unconfined) ")], "named, no attachment path"
    assert re.search(r"^\s*userns,\s*$", profile, re.M)


# ── the privileged runner ────────────────────────────────────────────────────────────────────────────────────────


def test_the_privileged_runner_is_the_old_docker_under_a_label_of_its_own():
    base = _rendered(ci_runner.COMPOSE_FILE, suffix="_privileged")
    over = _rendered(ci_runner.COMPOSE_FILE, ci_runner.PRIVILEGED_FILE, suffix="_privileged")
    daemon, runner = over["services"]["docker"], over["services"]["runner"]

    assert daemon["privileged"] is True and "user" not in daemon, "docker:dind runs as root"
    assert daemon["image"] == base["services"]["docker"]["image"].removesuffix("-rootless"), "the same Docker"
    assert {v["target"]: v["source"] for v in daemon["volumes"]}["/var/lib/docker"] == "docker-data"
    assert runner["environment"]["LABELS"] == "andros-ubuntu-nex-demo-privileged"
    assert runner["environment"]["LABELS"] != base["services"]["runner"]["environment"]["LABELS"], (
        "never the project's label — no other job may land there"
    )
    expected = {k: v for k, v in base["services"].items() if k != "docker"}
    expected["runner"]["environment"]["LABELS"] = runner["environment"]["LABELS"]
    assert {k: v for k, v in over["services"].items() if k != "docker"} == expected, "nothing else differs"


@pytest.mark.parametrize(
    ("runs_on", "needs"),
    [
        ("andros-ubuntu-nex-demo-privileged", True),
        ("[self-hosted, andros-ubuntu-nex-demo-privileged]", True),
        ("andros-ubuntu-nex-demo", False),
        ("andros-ubuntu-nex-demo-2-privileged", False),
        ("andros-ubuntu-nex-demo  # not andros-ubuntu-nex-demo-privileged", False),
    ],
    ids=["named", "in-a-list", "the-projects-label", "another-projects", "only-in-a-comment"],
)
def test_a_project_needs_the_privileged_runner_exactly_while_a_workflow_names_its_label(projects, runs_on, needs):
    _workflow(projects, SLUG, runs_on)

    assert ci_runner.needs_privileged(SLUG) is needs
    assert ci_runner.project_runners(SLUG) == ([1, ci_runner.PRIVILEGED] if needs else [1])


def test_a_project_without_workflows_needs_none(projects):
    assert ci_runner.needs_privileged(SLUG) is False


def _current(index: int) -> dict[str, str]:
    return {
        "com.docker.compose.project": ci_runner.compose_project(SLUG, index),
        ci_runner.TEMPLATE_LABEL: ci_runner.template_fingerprint(index),
    }


def _work(calls) -> list[str]:
    out = []
    for cmd, kw in calls:
        if cmd[1] in READS:
            continue
        env = kw.get("env") or {}
        who = f"[{env['RUNNER_SUFFIX'] or '1'}] " if "RUNNER_SUFFIX" in env else ""
        token = " +token" if env.get("RUNNER_TOKEN") else ""
        text = " ".join(cmd).replace(str(ci_runner.PRIVILEGED_FILE), "PRIVILEGED")
        out.append(who + text.replace(str(ci_runner.COMPOSE_FILE), "TEMPLATE") + token)
    return out


def test_founding_starts_the_privileged_runner_from_both_files_with_the_one_token(monkeypatch, projects):
    _workflow(projects, SLUG, "andros-ubuntu-nex-demo-privileged")
    calls, tokens = _docker(monkeypatch, {})

    ci_runner.provision(SLUG, "acme/nex-demo")

    assert _work(calls) == [
        "[1] docker compose --file TEMPLATE run --rm --no-TTY runner true +token",
        "[1] docker compose --file TEMPLATE up -d --wait",
        "[_privileged] docker compose --file TEMPLATE --file PRIVILEGED run --rm --no-TTY runner true +token",
        "[_privileged] docker compose --file TEMPLATE --file PRIVILEGED up -d --wait",
    ]
    assert tokens == ["acme/nex-demo"]


def test_the_privileged_runner_has_a_fingerprint_of_its_own():
    assert ci_runner.template_fingerprint(ci_runner.PRIVILEGED) != ci_runner.template_fingerprint()
    assert ci_runner.compose_env(SLUG, "acme/nex-demo", ci_runner.PRIVILEGED)["CI_RUNNER_TEMPLATE"] == (
        ci_runner.template_fingerprint(ci_runner.PRIVILEGED)
    )


@pytest.mark.parametrize(
    ("asks", "existing", "state"),
    [
        (True, {"nex-ci-runner-nex-demo": _current(1)}, ci_runner.OUTDATED),
        (
            True,
            {"nex-ci-runner-nex-demo": _current(1), "nex-ci-runner-nex-demo_privileged": _current(0)},
            ci_runner.CURRENT,
        ),
        (
            False,
            {"nex-ci-runner-nex-demo": _current(1), "nex-ci-runner-nex-demo_privileged": _current(0)},
            ci_runner.OUTDATED,
        ),
        (
            True,
            {
                "nex-ci-runner-nex-demo": _current(1),
                "nex-ci-runner-nex-demo_privileged": {
                    "com.docker.compose.project": "nex-ci-nex-demo_privileged",
                    ci_runner.TEMPLATE_LABEL: ci_runner.template_fingerprint(),
                },
            },
            ci_runner.OUTDATED,
        ),
    ],
    ids=["asked-missing", "asked-there", "there-unasked", "there-from-the-rootless-template"],
)
def test_the_state_counts_the_privileged_runner(monkeypatch, projects, asks, existing, state):
    _workflow(projects, SLUG, "andros-ubuntu-nex-demo-privileged" if asks else "andros-ubuntu-nex-demo")
    _docker(monkeypatch, existing)

    assert ci_runner.state(SLUG) == state


def test_the_privileged_runner_goes_once_no_workflow_asks_for_it(monkeypatch, projects):
    _workflow(projects, SLUG, "andros-ubuntu-nex-demo")
    calls, _tokens = _docker(
        monkeypatch, {"nex-ci-runner-nex-demo": _current(1), "nex-ci-runner-nex-demo_privileged": _current(0)}
    )

    ci_runner.provision(SLUG, "acme/nex-demo")

    assert _work(calls) == [
        "[1] docker compose --file TEMPLATE up -d --wait",
        "docker compose --project-name nex-ci-nex-demo_privileged down --volumes",
    ]


def test_github_is_asked_about_the_privileged_runner_too(projects):
    _workflow(projects, SLUG, "andros-ubuntu-nex-demo-privileged")

    assert ci_runner.runner_names(SLUG) == ["andros-ubuntu-nex-demo", "andros-ubuntu-nex-demo_privileged"]


def test_deleting_a_project_removes_its_privileged_runner(monkeypatch, projects):
    """Also while its workflow still asks for it — the checkout may outlive the project by a moment."""
    _workflow(projects, SLUG, "andros-ubuntu-nex-demo-privileged")
    calls, _tokens = _docker(
        monkeypatch, {"nex-ci-runner-nex-demo": _current(1), "nex-ci-runner-nex-demo_privileged": _current(0)}
    )

    ci_runner.deprovision(SLUG)

    assert _work(calls) == [
        "docker compose --project-name nex-ci-nex-demo_privileged down --volumes",
        "docker compose --project-name nex-ci-nex-demo down --volumes",
    ]


def test_the_runner_tab_counts_a_job_on_the_privileged_runner_as_the_projects_own(tmp_path):
    """The tab lists the jobs that run anywhere but on the project's runner — the privileged one is the project's."""
    from backend.services import project_ci_runner as pcr

    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "jobs:\n"
        "  smb-blackhole:\n    runs-on: andros-ubuntu-nex-demo-privileged\n"
        "  test:\n    runs-on: andros-ubuntu-nex-demo\n"
        "  foreign:\n    runs-on: andros-ubuntu-nex-other-privileged\n",
        encoding="utf-8",
    )

    found, unreadable = pcr.jobs_elsewhere(tmp_path, SLUG)

    assert [(j.job, j.runs_on) for j in found] == [("foreign", "andros-ubuntu-nex-other-privileged")]
    assert unreadable == []

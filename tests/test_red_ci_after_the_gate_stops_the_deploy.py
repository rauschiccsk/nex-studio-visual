"""DEV-51 — the gate waits for the runs its own version tag starts, and red CI closes the deploy.

10.10.2026, NEX Inbox 1.7.0 (UTC):

  08:57      CI #154 and Release smoke gate #20 on commit 48de080 — both green
  09:47:11   the cockpit pushed the version tag ``v1.7.0`` to that commit
  09:47:13   the gate: "Kontroly projektu: CI zelené" — judged on the two runs from 08:57
  09:47:15   the tag started Release smoke gate #21 (``push: tags: ['v*']``)
  ~10:00     #21 failed; the screen showed "Zostavenie zlyhalo" next to "Hotovo — pripravené na nasadenie",
             and nothing stopped a deploy.

Director 10.10.2026: „Kokpit nex-inbox hlásil že CI je zelené, pozrel som na GitHub a ja vidím, že je
červené“ → „Áno, založ tiket do DEV a implementuj“.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.schemas.customer import CustomerCreate
from backend.services import ci_status, orchestrator
from backend.services import customer as customer_service
from backend.services import deploy as deploy_service

TAG = "v1.7.0"
RELEASE_GATE = "on:\n  push:\n    tags: ['v*']\n  workflow_dispatch:\nname: Release smoke gate\njobs: {}\n"
BRANCH_CI = "name: CI\non:\n  push:\n    branches: [main]\n  pull_request:\n    branches: [main]\njobs: {}\n"


def _project(tmp_path: Path, *workflows: tuple[str, str]) -> Path:
    root = tmp_path / "projekt"
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    for name, text in workflows:
        (wf / name).write_text(text, encoding="utf-8")
    return root


def _run(workflow, run_id, *, branch="main", event="push", status="completed", conclusion="success"):
    return {
        "workflowName": workflow,
        "databaseId": run_id,
        "headBranch": branch,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "url": f"https://github.com/rauschiccsk/nex-inbox/actions/runs/{run_id}",
    }


#: What GitHub answered about commit 48de080, question by question, on 10.10.2026.
OLD_GREEN = [
    _run("CI", 38039656266),
    _run("Release smoke gate", 38039661863, event="workflow_dispatch"),
]
INCIDENT = [
    OLD_GREEN,
    OLD_GREEN,
    [*OLD_GREEN, _run("Release smoke gate", 38042619283, branch=TAG, status="queued", conclusion=None)],
    [*OLD_GREEN, _run("Release smoke gate", 38042619283, branch=TAG, status="in_progress", conclusion=None)],
    [*OLD_GREEN, _run("Release smoke gate", 38042619283, branch=TAG, conclusion="failure")],
]


def _github(monkeypatch, answers):
    """GitHub answering differently each time it is asked — the gate's one subprocess seam. The suite says
    "no checks" by default so nothing asks the real GitHub (``tests/conftest.py``); these projects have them."""
    monkeypatch.setattr(orchestrator, "_project_has_ci", lambda root: (root / ".github" / "workflows").is_dir())
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "48de08076e594d4b224281b1befd0a5ba0fa9ca3")
    monkeypatch.setattr(orchestrator, "CI_RUN_APPEAR_INTERVAL", 0)
    monkeypatch.setattr(orchestrator, "CI_RUN_FINISH_INTERVAL", 0)
    asked: list[int] = []

    async def fake_step(cmd, timeout):
        if "config" in cmd:
            return 0, "https://github.com/rauschiccsk/nex-inbox.git\n"
        asked.append(1)
        return 0, json.dumps(answers[min(len(asked) - 1, len(answers) - 1)])

    monkeypatch.setattr(orchestrator, "_run_publish_step", fake_step)
    return asked


# ── 1. which workflows a version tag starts ────────────────────────────────────


@pytest.mark.parametrize(
    ("workflow", "started"),
    [
        (RELEASE_GATE, ["Release smoke gate"]),
        ("name: G\non:\n  push:\n    tags: ['v*.*.*']\n", ["G"]),
        ("name: G\non:\n  push:\n    tags: ['release-*']\n", []),
        ("name: G\non:\n  push:\n    tags: ['v*', '!v1.7.*']\n", []),
        ("name: G\non:\n  push:\n    tags-ignore: ['v*']\n", []),
        (BRANCH_CI, []),
        ("name: G\non: push\n", ["G"]),
        ("name: G\non: [push, pull_request]\n", ["G"]),
        ("name: G\non:\n  push:\n", ["G"]),
        ("name: G\non:\n  push:\n    paths: ['backend/**']\n", ["G"]),
        ("name: G\non:\n  workflow_dispatch:\n", []),
    ],
)
def test_a_tag_push_starts_exactly_the_workflows_github_would_start(tmp_path, workflow, started):
    root = _project(tmp_path, ("w.yml", workflow))

    assert ci_status.postupy_spustene_znackou(root, TAG) == started


def test_a_workflow_without_a_name_is_known_by_its_path(tmp_path):
    root = _project(tmp_path, ("release.yml", "on:\n  push:\n    tags: ['v*']\n"))

    assert ci_status.postupy_spustene_znackou(root, TAG) == [".github/workflows/release.yml"]


# ── 2. the gate waits for the run its tag starts ───────────────────────────────


async def test_the_incident_the_gate_waits_for_the_tags_own_run_and_sees_it_fail(tmp_path, monkeypatch):
    root = _project(tmp_path, ("ci.yml", BRANCH_CI), ("release-smoke.yml", RELEASE_GATE))
    asked = _github(monkeypatch, INCIDENT)

    state, detail = await orchestrator._ci_status_for_head(root, release_tag=TAG)

    assert state == "red", f"brána vyhlásila „{detail}“ z behov spred hodiny"
    assert "38042619283" in detail and "Release smoke gate" in detail
    assert len(asked) == len(INCIDENT), "brána sa prestala pýtať skôr, než beh značky dobehol"


async def test_without_the_tag_the_gate_is_what_it_was_on_10_october(tmp_path, monkeypatch):
    """The same answers, no tag: the two old green runs settle it after the first question — the bug itself."""
    root = _project(tmp_path, ("ci.yml", BRANCH_CI), ("release-smoke.yml", RELEASE_GATE))
    _github(monkeypatch, INCIDENT)

    state, _detail = await orchestrator._ci_status_for_head(root)

    assert state == "green"


async def test_a_project_whose_tag_starts_nothing_is_not_kept_waiting(tmp_path, monkeypatch):
    root = _project(tmp_path, ("ci.yml", BRANCH_CI))
    asked = _github(monkeypatch, [OLD_GREEN])

    state, _detail = await orchestrator._ci_status_for_head(root, release_tag=TAG)

    assert state == "green"
    assert len(asked) == 1, "brána čakala na beh, ktorý značka nespustí"


async def test_a_tag_run_that_never_appears_is_named_not_mistaken_for_green(tmp_path, monkeypatch):
    root = _project(tmp_path, ("ci.yml", BRANCH_CI), ("release-smoke.yml", RELEASE_GATE))
    _github(monkeypatch, [OLD_GREEN])

    state, detail = await orchestrator._ci_status_for_head(root, release_tag=TAG)

    assert state == "unknown"
    assert "Release smoke gate" in detail and TAG in detail and "neobjavil" in detail


def _git(root: Path, *args: str) -> str:
    cmd = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


def test_the_gate_names_the_tag_only_when_it_is_on_the_commit_being_judged(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "commit", "-q", "--allow-empty", "-m", "verzia")
    _git(root, "tag", "-a", TAG, "-m", "NEX Studio: v1.7.0 verified")

    assert orchestrator._release_tag_at_head(root, "1.7.0") == TAG
    assert orchestrator._release_tag_at_head(root, "1.6.0") is None

    _git(root, "commit", "-q", "--allow-empty", "-m", "neskôr")
    assert orchestrator._release_tag_at_head(root, "1.7.0") is None, "značka iného commitu sa nečaká"


async def test_the_verdict_settle_hands_the_tag_to_the_gate(db_session, monkeypatch):
    from tests.test_orchestrator_v2_verifikacia import _make_version, _seed_done_tasks, _seed_verifikacia

    version, project = _make_version(db_session, project_dial="po_kazdej_faze")
    state = _seed_verifikacia(db_session, version.id, iteration=0)
    state.status = "awaiting_manazer"
    db_session.flush()
    _seed_done_tasks(db_session, version, project, ["T1"])
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "deadbeefcafe")
    monkeypatch.setattr(orchestrator, "_git_tag_version", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_release_tag_at_head", lambda root, number: f"v{number}")
    seen: dict = {}

    async def _gate(root, **kwargs):
        seen.update(kwargs)
        return "green", "CI zelené (postup CI, beh 1)"

    monkeypatch.setattr(orchestrator, "_ci_status_for_head", _gate)

    await orchestrator.apply_action(db_session, version_id=version.id, action="verdict", payload={"verdict": "PASS"})

    assert seen.get("release_tag") == f"v{version.version_number}"


# ── 3. red CI on the code that would be deployed closes Nasadiť ────────────────


def _ci(stav, *, bezi=False, detail="CI zlyhalo (postup Release smoke gate, beh 38042619283, failure)"):
    return ci_status.StavZostavenia(
        stav, detail, "48de080", 0.0, bezi=bezi, url="https://github.com/x/y/actions/runs/38042619283"
    )


def _finished_version(db) -> tuple[Project, Version]:
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.sk", password_hash="x", role="ri"
    )
    db.add(user)
    db.flush()
    project = Project(
        name="NEX Inbox skúška",
        slug=f"ci-red-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="DEV-51",
        created_by=user.id,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="1.7.0", name="1.7.0", status="active")
    db.add(version)
    db.flush()
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="done",
            current_actor="auditor",
            status="done",
            next_action="",
        )
    )
    orchestrator._record_message(
        db,
        version_id=version.id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content="PASS",
        payload={"verdict": "PASS", "phase": "verifikacia"},
    )
    db.flush()
    return project, version


@pytest.mark.parametrize(
    ("ci", "cause"),
    [
        (_ci("red"), "ci_red"),
        (_ci("unknown", bezi=True, detail="CI ešte beží (postup Release smoke gate, beh 38042619283)"), "ci_running"),
    ],
)
def test_red_or_running_ci_closes_nasadit_and_says_why(db_session, ci, cause):
    project, version = _finished_version(db_session)
    assert deploy_service.list_verified_versions(db_session, project.id) == ["1.7.0"], "príprava: verzia je hotová"

    matrix = deploy_service.build_matrix(db_session, project, ci=ci)

    assert matrix["verified_versions"] == [], "červené CI, a verzia sa dá nasadiť"
    block = matrix["deployability"]
    assert (block["cause"], block["version_number"], block["version_id"]) == (cause, "1.7.0", version.id)
    assert block["ci_detail"] == ci.detail and block["ci_url"] == ci.url
    assert block["can_reverify"] is False


@pytest.mark.parametrize("ci", [_ci("green", detail="CI zelené"), _ci("unknown", detail="GitHub neodpovedal"), None])
def test_green_or_unknown_ci_leaves_nasadit_open(db_session, ci):
    project, _version = _finished_version(db_session)

    matrix = deploy_service.build_matrix(db_session, project, ci=ci)

    assert matrix["verified_versions"] == ["1.7.0"]
    assert matrix["deployability"]["cause"] == "ok"


async def _deploy(db, monkeypatch, ci, *, with_workflows=True, tmp_path):
    project, _version = _finished_version(db)
    customer = customer_service.create(
        db, project.id, CustomerCreate(name="MÁGERSTAV", slug="mager", subdomain="mager")
    )
    root = tmp_path / project.slug
    if with_workflows:
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "ci.yml").write_text(BRANCH_CI, encoding="utf-8")
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(orchestrator, "_project_has_ci", lambda root: (root / ".github" / "workflows").is_dir())
    asked: list[dict] = []

    async def _stav(project_root, sha=None, *, cerstvy=False):
        asked.append({"root": project_root, "cerstvy": cerstvy})
        return ci

    monkeypatch.setattr(ci_status, "stav_commitu", _stav)
    ran: list[int] = []

    async def _runner(**_kwargs):
        ran.append(1)
        return True, "OK", "https://uat-mager.isnex.eu"

    outcome = await deploy_service.deploy(
        db, customer.id, version_number="1.7.0", environment="uat", actor_id=project.created_by, deploy_runner=_runner
    )
    return outcome, ran, asked


@pytest.mark.parametrize(
    ("ci", "says"),
    [
        (_ci("red"), "zlyhali"),
        (_ci("unknown", bezi=True, detail="CI ešte beží (postup CI, beh 7)"), "ešte bežia"),
    ],
)
async def test_the_deploy_itself_refuses_red_or_running_ci(db_session, monkeypatch, tmp_path, ci, says):
    with pytest.raises(ValueError, match="Nasadenie zastavené") as refused:
        await _deploy(db_session, monkeypatch, ci, tmp_path=tmp_path)

    assert says in str(refused.value) and ci.detail in str(refused.value)


async def test_the_deploy_asks_github_fresh_about_the_projects_own_code(db_session, monkeypatch, tmp_path):
    outcome, ran, asked = await _deploy(db_session, monkeypatch, _ci("green", detail="CI zelené"), tmp_path=tmp_path)

    assert ran == [1] and list(outcome.warnings) == []
    assert asked and asked[0]["cerstvy"] is True, "nasadenie sa rozhodlo podľa zapamätanej odpovede"
    assert asked[0]["root"].name.startswith("ci-red-")


async def test_unknown_ci_deploys_but_says_so_where_the_project_has_checks(db_session, monkeypatch, tmp_path):
    unknown = _ci("unknown", detail="stav CI sa nepodarilo zistiť (GitHub neodpovedal)")

    outcome, ran, _asked = await _deploy(db_session, monkeypatch, unknown, tmp_path=tmp_path)
    assert ran == [1]
    assert any("nepodarilo potvrdiť" in w and unknown.detail in w for w in outcome.warnings)


async def test_a_project_without_checks_is_not_warned_about_them(db_session, monkeypatch, tmp_path):
    unknown = _ci("unknown", detail="pre tento commit sa zatiaľ neobjavil žiadny beh CI")

    outcome, ran, _asked = await _deploy(db_session, monkeypatch, unknown, with_workflows=False, tmp_path=tmp_path)

    assert ran == [1] and list(outcome.warnings) == []


# ── 4. the HTTP surface: the matrix reads CI, a refused deploy is a 409 ────────


def _as_owner(db, project):
    from backend.core.security import get_current_user, require_ri_role, require_shu_or_above
    from backend.main import app

    owner = db.get(User, project.created_by)
    for dep in (get_current_user, require_ri_role, require_shu_or_above):
        app.dependency_overrides[dep] = lambda owner=owner: owner


def test_the_deploy_screen_gets_the_ci_cause(client, db_session, monkeypatch):
    project, _version = _finished_version(db_session)
    _as_owner(db_session, project)

    async def _red(project_root, sha=None, *, cerstvy=False):
        return _ci("red")

    monkeypatch.setattr(ci_status, "stav_commitu", _red)

    body = client.get(f"/api/v1/projects/{project.slug}/deploy-matrix").json()

    assert body["verified_versions"] == []
    assert body["deployability"]["cause"] == "ci_red"
    assert "38042619283" in body["deployability"]["ci_detail"]


def test_a_refused_deploy_is_a_conflict_with_the_reason(client, db_session, monkeypatch):
    project, _version = _finished_version(db_session)
    customer = customer_service.create(
        db_session, project.id, CustomerCreate(name="MÁGERSTAV", slug="mager", subdomain="mager")
    )
    _as_owner(db_session, project)

    async def _red(project_root, sha=None, *, cerstvy=False):
        return _ci("red")

    monkeypatch.setattr(ci_status, "stav_commitu", _red)

    resp = client.post(
        f"/api/v1/customers/{customer.id}/deploy", json={"version_number": "1.7.0", "environment": "uat"}
    )

    assert resp.status_code == 409, resp.text
    assert "zlyhali" in resp.json()["detail"]

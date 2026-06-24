"""v0.8.0 — Engine-owned GitHub release publish (CR-1/CR-2/CR-3) unit tests.

The Coordinator finalizes a release LOCALLY (no GitHub creds in its headless env); the ENGINE (which
has ``GH_TOKEN``) publishes. Three layers:

* **The runner** (:func:`orchestrator._run_release_publish` + helpers) — ``git push`` + a CI watch via
  the single ``_run_publish_step`` subprocess seam (faked here; ``git``/``gh`` are never spawned):
  push-fail → ``(False, …)``; push-ok + CI-green → ``(True, …)``; push-ok + CI-red → ``(False, …)``;
  push-ok + CI watch-timeout → ``(True, "… still running …")`` (never false-block a slow CI). The CI run
  is matched on the pushed HEAD sha, so a stale green run can never be mistaken for this release's CI.

* **The auto-publish** (:func:`orchestrator._release_auto_publish`) — modelled on
  ``_fast_fix_auto_deploy``: resolves ``project.repo_url`` (NULL → graceful skip + awaiting_director),
  runs the runner, records a ``system→director`` notification, settles success → awaiting_director /
  failure → blocked.

* **The action** (CR-3) — ``retry_publish`` is offered ONLY at a ``new_version`` release/blocked (absent
  for fast_fix / cr / bug), and ``apply_action`` re-runs the publish.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator

# ---------------------------------------------------------------------------
# Runner: _run_release_publish + helpers (the _run_publish_step subprocess seam is faked)
# ---------------------------------------------------------------------------

HEAD_SHA = "abc123def456abc123def456abc123def456abcd"
REPO = "rauschiccsk/nex-demo"


class _StepRecorder:
    """Fake for ``orchestrator._run_publish_step``: scripts ``(rc, out)`` per git/gh step keyed by a
    token in the command, and records every command. Unknown steps default to PASS."""

    def __init__(self, results: dict[str, tuple[int, str]]) -> None:
        self._results = results
        self.calls: list[list[str]] = []

    async def __call__(self, cmd: list[str], timeout: int) -> tuple[int, str]:
        self.calls.append(cmd)
        if "setup-git" in cmd:
            key = "setup"
        elif "push" in cmd:
            key = "push"
        elif "rev-parse" in cmd:
            key = "revparse"
        elif "list" in cmd:
            key = "list"
        elif "watch" in cmd:
            key = "watch"
        else:
            key = "other"
        return self._results.get(key, (0, "ok"))

    def ran(self, token: str) -> bool:
        return any(token in cmd for cmd in self.calls)


def _ci_listed(run_id: int, head_sha: str = HEAD_SHA) -> str:
    return json.dumps([{"databaseId": run_id, "headSha": head_sha}])


@pytest.fixture()
def _no_sleep(monkeypatch):
    async def _sleep(*_a, **_k):
        return None

    monkeypatch.setattr(orchestrator.asyncio, "sleep", _sleep)


@pytest.mark.asyncio
async def test_publish_push_fail_returns_false_no_ci(monkeypatch, _no_sleep) -> None:
    """``git push`` fails after retries → ``(False, "git push failed: …")``, and CI is never touched."""
    rec = _StepRecorder({"push": (1, "fatal: Authentication failed for 'https://github.com/...'")})
    monkeypatch.setattr(orchestrator, "_run_publish_step", rec)

    ok, detail = await orchestrator._run_release_publish("demo", REPO)

    assert ok is False
    assert detail.startswith("git push failed:")
    assert "Authentication failed" in detail
    assert not rec.ran("watch") and not rec.ran("list"), "a push failure must never reach the CI watch"


@pytest.mark.asyncio
async def test_publish_push_ok_ci_green(monkeypatch, _no_sleep) -> None:
    """push ok → resolve the run for the pushed HEAD → CI green → ``(True, "published + CI green (id)")``."""
    rec = _StepRecorder(
        {
            "push": (0, "Everything up-to-date"),
            "revparse": (0, f"{HEAD_SHA}\n"),
            "list": (0, _ci_listed(777)),
            "watch": (0, "✓ build  ✓ test"),
        }
    )
    monkeypatch.setattr(orchestrator, "_run_publish_step", rec)

    ok, detail = await orchestrator._run_release_publish("demo", REPO)

    assert (ok, detail) == (True, "published + CI green (777)")
    # The watch targets the run matched on the pushed HEAD sha (not "the latest run").
    watch_cmd = next(cmd for cmd in rec.calls if "watch" in cmd)
    assert "777" in watch_cmd and "--exit-status" in watch_cmd and REPO in watch_cmd


@pytest.mark.asyncio
async def test_publish_push_ok_ci_red(monkeypatch, _no_sleep) -> None:
    """push ok but CI concludes red (watch exit 1) → ``(False, "CI failed (id): …")`` — surfaced."""
    rec = _StepRecorder(
        {
            "push": (0, ""),
            "revparse": (0, f"{HEAD_SHA}\n"),
            "list": (0, _ci_listed(888)),
            "watch": (1, "X test  the build failed"),
        }
    )
    monkeypatch.setattr(orchestrator, "_run_publish_step", rec)

    ok, detail = await orchestrator._run_release_publish("demo", REPO)

    assert ok is False
    assert detail.startswith("CI failed (888):")
    assert "build failed" in detail


@pytest.mark.asyncio
async def test_publish_push_ok_ci_watch_timeout_still_running(monkeypatch, _no_sleep) -> None:
    """push ok but the CI watch times out (sentinel 124) → ``(True, "… still running …")`` — the push
    SUCCEEDED, so a slow CI must NOT false-block the release."""
    rec = _StepRecorder(
        {
            "push": (0, ""),
            "revparse": (0, f"{HEAD_SHA}\n"),
            "list": (0, _ci_listed(999)),
            "watch": (124, f"timeout ({orchestrator.RELEASE_PUBLISH_TIMEOUT}s)"),
        }
    )
    monkeypatch.setattr(orchestrator, "_run_publish_step", rec)

    ok, detail = await orchestrator._run_release_publish("demo", REPO)

    assert ok is True
    assert detail == "pushed; CI still running (999) — monitor"


@pytest.mark.asyncio
async def test_publish_push_ok_run_never_registers_still_running(monkeypatch, _no_sleep) -> None:
    """push ok but no run ever matches the pushed HEAD (registration never observed) → ``(True, "… still
    running …")`` (push succeeded; never block on an undeterminable CI), and the watch is never invoked."""
    rec = _StepRecorder(
        {
            "push": (0, ""),
            "revparse": (0, f"{HEAD_SHA}\n"),
            "list": (0, "[]"),  # no runs match the pushed sha, every poll
        }
    )
    monkeypatch.setattr(orchestrator, "_run_publish_step", rec)

    ok, detail = await orchestrator._run_release_publish("demo", REPO)

    assert ok is True
    assert "still running" in detail
    assert not rec.ran("watch"), "an unresolved run id must not invoke gh run watch"


@pytest.mark.asyncio
async def test_publish_no_token_value_in_calls(monkeypatch, _no_sleep) -> None:
    """§4: the publish wires creds via ``gh auth setup-git`` ONLY — no command ever carries a token VALUE
    (the GH_TOKEN stays in the inherited env, never in argv)."""
    rec = _StepRecorder(
        {"push": (0, ""), "revparse": (0, f"{HEAD_SHA}\n"), "list": (0, _ci_listed(1)), "watch": (0, "")}
    )
    monkeypatch.setattr(orchestrator, "_run_publish_step", rec)

    await orchestrator._run_release_publish("demo", REPO)

    assert rec.ran("setup-git"), "the credential helper is wired via gh auth setup-git"
    for cmd in rec.calls:
        joined = " ".join(cmd).lower()
        assert "ghp_" not in joined and "github_pat" not in joined, "no token value may appear in argv"


# ---------------------------------------------------------------------------
# Auto-publish: _release_auto_publish (DB-backed) + the retry_publish action
# ---------------------------------------------------------------------------


def _seed(
    db,
    *,
    repo_url,
    uat_slug: str | None = None,
    flow_type: str = "new_version",
    stage: str = "release",
    status: str = "agent_working",
):
    creator = User(
        username=f"rp_{uuid.uuid4().hex[:8]}",
        email=f"rp_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(creator)
    db.flush()
    project = Project(
        name=f"Publish Fixture {uuid.uuid4().hex[:6]}",
        slug=f"pub-{uuid.uuid4().hex[:8]}",
        category="multimodule",
        description="v0.8.0 release-publish fixture.",
        repo_url=repo_url,
        uat_slug=uat_slug,
        created_by=creator.id,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="v0.8.0", status="active")
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type=flow_type,
        current_stage=stage,
        current_actor="coordinator",
        status=status,
        dispatch_in_flight=False,
    )
    db.add(state)
    db.flush()
    return version.id, state


def _director_notes(db, version_id):
    # seq-ordered (insertion order) like every production query — created_at ties within a transaction
    # (func.now() is constant), so a bare SELECT returns same-timestamp rows in a non-deterministic order
    # and ``[-1]`` (latest note) flakes. The seq tie-break makes "the last director note" deterministic.
    return (
        db.execute(
            select(PipelineMessage)
            .where(
                PipelineMessage.version_id == version_id,
                PipelineMessage.author == "system",
                PipelineMessage.recipient == "director",
            )
            .order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


@pytest.mark.asyncio
async def test_auto_publish_success_records_note_and_chains_uat_deploy(db_session, monkeypatch) -> None:
    """A successful publish records the green outcome AND chains the engine UAT-deploy (v0.8.1 CR-1) —
    the chained step owns the final settle."""
    version_id, state = _seed(db_session, repo_url="https://github.com/rauschiccsk/nex-demo")
    chained = {"called": False}

    async def _pub(slug, repo):
        assert repo == "rauschiccsk/nex-demo"
        return True, "published + CI green (123)"

    async def _deploy(db, st, *, on_message=None):
        chained["called"] = True
        st.status = "awaiting_director"
        st.next_action = "Nasadené na UAT — over a akceptuj."

    monkeypatch.setattr(orchestrator, "_run_release_publish", _pub)
    monkeypatch.setattr(orchestrator, "_release_auto_uat_deploy", _deploy)

    await orchestrator._release_auto_publish(db_session, state)

    assert chained["called"] is True, "publish-ok must chain the engine UAT-deploy"
    assert state.status == "awaiting_director"
    # The publish-ok notification stays on the board (the chained deploy adds its own note).
    notes = _director_notes(db_session, version_id)
    assert any(
        n.payload
        == {"release_publish": {"repo": "rauschiccsk/nex-demo", "ok": True, "detail": "published + CI green (123)"}}
        for n in notes
    )


@pytest.mark.asyncio
async def test_auto_publish_failure_blocked_no_uat_deploy(db_session, monkeypatch) -> None:
    """A failed publish settles to blocked (block_reason=system_error), surfaced, and NEVER chains the
    UAT-deploy (the publish step blocks as before)."""
    version_id, state = _seed(db_session, repo_url="https://github.com/rauschiccsk/nex-demo")

    async def _pub(slug, repo):
        return False, "git push failed: fatal: Authentication failed"

    async def _deploy(db, st, *, on_message=None):
        raise AssertionError("a failed publish must NOT chain the UAT-deploy")

    monkeypatch.setattr(orchestrator, "_run_release_publish", _pub)
    monkeypatch.setattr(orchestrator, "_release_auto_uat_deploy", _deploy)

    await orchestrator._release_auto_publish(db_session, state)

    assert state.status == "blocked"
    assert state.block_reason == "system_error"
    assert "GitHub publish/CI zlyhal" in state.next_action
    assert "Authentication failed" in state.next_action
    assert len(_director_notes(db_session, version_id)) == 1


@pytest.mark.asyncio
async def test_auto_publish_null_repo_url_skips_gracefully(db_session, monkeypatch) -> None:
    """No ``repo_url`` → graceful SKIP (awaiting_director), the runner is NEVER invoked."""
    version_id, state = _seed(db_session, repo_url=None)
    called = {"run": False}

    async def _pub(slug, repo):
        called["run"] = True
        return True, "x"

    async def _deploy(db, st, *, on_message=None):
        raise AssertionError("a NULL repo_url skips publish BEFORE the chain — the UAT-deploy must not run")

    monkeypatch.setattr(orchestrator, "_run_release_publish", _pub)
    monkeypatch.setattr(orchestrator, "_release_auto_uat_deploy", _deploy)

    await orchestrator._release_auto_publish(db_session, state)

    assert state.status == "awaiting_director"
    assert called["run"] is False, "a NULL repo_url must skip the publish, not run it"
    notes = _director_notes(db_session, version_id)
    assert len(notes) == 1
    assert notes[0].payload == {"release_publish": {"skipped": True, "reason": "no_repo_url"}}


# ---------------------------------------------------------------------------
# CR-3: retry_publish offering (determine_available_actions) + apply_action
# ---------------------------------------------------------------------------


def _mk_state(stage: str, status: str, flow_type: str = "new_version") -> PipelineState:
    return PipelineState(
        version_id=uuid.uuid4(), flow_type=flow_type, current_stage=stage, current_actor="coordinator", status=status
    )


def test_retry_publish_offered_only_at_new_version_release_blocked() -> None:
    """retry_publish is offered at a new_version release/blocked, and NOWHERE else."""
    assert "retry_publish" in orchestrator.determine_available_actions(_mk_state("release", "blocked"))
    # awaiting (publish succeeded / not-yet-failed) → only uat_accept, no retry_publish.
    assert "retry_publish" not in orchestrator.determine_available_actions(_mk_state("release", "awaiting_director"))
    # absent for fast_fix / cr / bug (their release never engine-publishes — out of scope).
    assert "retry_publish" not in orchestrator.determine_available_actions(_mk_state("release", "blocked", "fast_fix"))
    assert "retry_publish" not in orchestrator.determine_available_actions(_mk_state("release", "blocked", "cr"))
    # not offered outside the release stage.
    assert "retry_publish" not in orchestrator.determine_available_actions(_mk_state("gate_g", "blocked"))


@pytest.mark.asyncio
async def test_apply_action_retry_publish_reruns_publish(db_session, monkeypatch) -> None:
    """apply_action(retry_publish) at release/blocked re-runs the publish; a success → awaiting_director."""
    version_id, _state = _seed(db_session, repo_url="https://github.com/rauschiccsk/nex-demo", status="blocked")

    async def _pub(slug, repo):
        return True, "published + CI green (321)"

    monkeypatch.setattr(orchestrator, "_run_release_publish", _pub)
    # publish-ok chains into _release_auto_uat_deploy — keep that an existing-compose redeploy (no provision).
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)

    async def _deploy(project_slug, uat_slug):
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)

    out = await orchestrator.apply_action(db_session, version_id=version_id, action="retry_publish")

    assert out.status == "awaiting_director"
    # A director→system directive note was recorded plus the engine's system→director outcome note.
    directive = (
        db_session.execute(
            select(PipelineMessage).where(
                PipelineMessage.version_id == version_id, PipelineMessage.payload == {"retry_publish": True}
            )
        )
        .scalars()
        .all()
    )
    assert len(directive) == 1


@pytest.mark.asyncio
async def test_apply_action_retry_publish_rejected_for_fast_fix(db_session, monkeypatch) -> None:
    """retry_publish is rejected for a fast_fix release (its lane never engine-publishes — out of scope)."""
    version_id, _state = _seed(
        db_session, repo_url="https://github.com/rauschiccsk/nex-demo", flow_type="fast_fix", status="blocked"
    )

    async def _pub(slug, repo):  # must never run
        raise AssertionError("fast_fix must never engine-publish")

    monkeypatch.setattr(orchestrator, "_run_release_publish", _pub)

    with pytest.raises(orchestrator.OrchestratorError, match="new_version"):
        await orchestrator.apply_action(db_session, version_id=version_id, action="retry_publish")


# ---------------------------------------------------------------------------
# v0.8.1 CR-1: full-flow engine UAT-deploy (_release_auto_uat_deploy) + chaining
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_flow_uat_deploy_runs_then_awaiting(db_session, monkeypatch) -> None:
    """uat_slug set + compose present → redeploy only (NO provision) → _run_uat_deploy runs → awaiting_director.

    CR-1: the notification content carries the uat-<slug>.isnex.eu URL; the payload shape is unchanged
    (ok / no skipped) so the v0.8.1 honest uat_accept keys on it."""
    version_id, state = _seed(db_session, repo_url="https://github.com/rauschiccsk/nex-demo", uat_slug="demo")
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)

    def _no_provision(*a, **kw):
        raise AssertionError("an existing compose must NOT be re-provisioned (redeploy only)")

    monkeypatch.setattr(orchestrator.uat_provisioner, "provision_uat", _no_provision)
    ran = {"slug": None}

    async def _deploy(project_slug, uat_slug):
        ran["slug"] = uat_slug
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)

    await orchestrator._release_auto_uat_deploy(db_session, state)

    assert ran["slug"] == "demo", "the shared _run_uat_deploy must run with the project's uat_slug"
    assert state.status == "awaiting_director"
    assert state.next_action == "Nasadené na UAT — over a akceptuj."
    notes = _director_notes(db_session, version_id)
    assert notes[-1].payload == {"uat_deploy": {"uat_slug": "demo", "ok": True, "detail": "OK"}}
    assert "https://uat-demo.isnex.eu" in notes[-1].content  # CR-1: URL surfaced to the Director
    assert "UAT akceptované" not in notes[-1].content


@pytest.mark.asyncio
async def test_full_flow_uat_deploy_failure_blocks(db_session, monkeypatch) -> None:
    """A failed UAT-deploy → blocked (block_reason=system_error) with the deploy error surfaced."""
    version_id, state = _seed(db_session, repo_url="https://github.com/rauschiccsk/nex-demo", uat_slug="demo")
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)

    async def _deploy(project_slug, uat_slug):
        return False, "exit 1: docker build failed"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)

    await orchestrator._release_auto_uat_deploy(db_session, state)

    assert state.status == "blocked"
    assert state.block_reason == "system_error"
    assert "UAT deploy zlyhal" in state.next_action and "docker build failed" in state.next_action


@pytest.mark.asyncio
async def test_full_flow_uat_deploy_null_slug_auto_provisions(db_session, monkeypatch) -> None:
    """v0.9.0 CR-1: uat_slug NULL → derive + PERSIST uat_slug, provision the UAT, then deploy → awaiting_director
    with the URL (replaces the old honest-skip)."""
    version_id, state = _seed(db_session, repo_url="https://github.com/rauschiccsk/nex-demo", uat_slug=None)
    project = db_session.execute(
        select(Project).join(Version, Version.project_id == Project.id).where(Version.id == version_id)
    ).scalar_one()
    expected_slug = project.slug  # no leading nex- → derive returns it unchanged
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: False)  # first release → provision

    prov = {"args": None}

    def _provision(project_slug, uat_slug, *, version):
        prov["args"] = (project_slug, uat_slug, version)
        return object()  # return value unused by CR-1

    monkeypatch.setattr(orchestrator.uat_provisioner, "provision_uat", _provision)

    ran = {"slug": None}

    async def _deploy(project_slug, uat_slug):
        ran["slug"] = uat_slug
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)

    await orchestrator._release_auto_uat_deploy(db_session, state)

    # uat_slug derived + persisted on the Project row.
    db_session.refresh(project)
    assert project.uat_slug == expected_slug
    # provision_uat called with the derived slug + the version label, then _run_uat_deploy.
    assert prov["args"] == (expected_slug, expected_slug, "v0.8.0")
    assert ran["slug"] == expected_slug
    assert state.status == "awaiting_director"
    assert state.next_action == "Nasadené na UAT — over a akceptuj."
    note = _director_notes(db_session, version_id)[-1]
    assert note.payload == {"uat_deploy": {"uat_slug": expected_slug, "ok": True, "detail": "OK"}}
    assert f"https://uat-{expected_slug}.isnex.eu" in note.content


@pytest.mark.asyncio
async def test_full_flow_uat_deploy_compose_missing_provisions(db_session, monkeypatch) -> None:
    """v0.9.0 CR-1: uat_slug set but compose missing → provision (NOT skip) then deploy → awaiting_director."""
    version_id, state = _seed(db_session, repo_url="https://github.com/rauschiccsk/nex-demo", uat_slug="gone")
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: False)

    prov = {"called": False}

    def _provision(project_slug, uat_slug, *, version):
        prov["called"] = True
        assert uat_slug == "gone"
        return object()

    monkeypatch.setattr(orchestrator.uat_provisioner, "provision_uat", _provision)

    async def _deploy(project_slug, uat_slug):
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)

    await orchestrator._release_auto_uat_deploy(db_session, state)

    assert prov["called"] is True
    assert state.status == "awaiting_director"
    note = _director_notes(db_session, version_id)[-1]
    assert note.payload == {"uat_deploy": {"uat_slug": "gone", "ok": True, "detail": "OK"}}
    assert "https://uat-gone.isnex.eu" in note.content


@pytest.mark.asyncio
async def test_full_flow_uat_provision_failure_blocks(db_session, monkeypatch) -> None:
    """v0.9.0 CR-1: a provision failure → blocked (block_reason=system_error), the deploy NEVER runs, never silent."""
    version_id, state = _seed(db_session, repo_url="https://github.com/rauschiccsk/nex-demo", uat_slug="gone")
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: False)

    def _provision(project_slug, uat_slug, *, version):
        raise RuntimeError("source docker-compose.yml not found")

    monkeypatch.setattr(orchestrator.uat_provisioner, "provision_uat", _provision)

    async def _deploy(project_slug, uat_slug):
        raise AssertionError("a provision failure must NOT proceed to deploy")

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)

    await orchestrator._release_auto_uat_deploy(db_session, state)

    assert state.status == "blocked"
    assert state.block_reason == "system_error"
    assert "UAT provisioning zlyhal" in state.next_action and "not found" in state.next_action
    note = _director_notes(db_session, version_id)[-1]
    assert note.payload["uat_deploy"]["ok"] is False
    assert note.payload["uat_deploy"]["provisioned"] is False


@pytest.mark.asyncio
async def test_publish_ok_chains_real_uat_deploy_end_to_end(db_session, monkeypatch) -> None:
    """End-to-end: _release_auto_publish (publish-ok) chains the REAL _release_auto_uat_deploy → with a
    uat_slug set + deploy ok, the final settle is 'Nasadené na UAT' and BOTH notes are on the board."""
    version_id, state = _seed(db_session, repo_url="https://github.com/rauschiccsk/nex-demo", uat_slug="demo")
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)

    async def _pub(slug, repo):
        return True, "published + CI green (5)"

    async def _deploy(project_slug, uat_slug):
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_release_publish", _pub)
    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)

    await orchestrator._release_auto_publish(db_session, state)

    assert state.status == "awaiting_director"
    assert state.next_action == "Nasadené na UAT — over a akceptuj."
    payloads = [n.payload for n in _director_notes(db_session, version_id)]
    assert {
        "release_publish": {"repo": "rauschiccsk/nex-demo", "ok": True, "detail": "published + CI green (5)"}
    } in payloads
    assert {"uat_deploy": {"uat_slug": "demo", "ok": True, "detail": "OK"}} in payloads


# ---------------------------------------------------------------------------
# v0.8.1 CR-2: honest uat_accept completion message — keyed on the ACTUAL deploy outcome
# (the latest uat_deploy notification), NOT the uat_slug proxy.
# ---------------------------------------------------------------------------

UAT_MSG = "UAT akceptované zákazníkom — pipeline dokončená."
NO_UAT_MSG = "Verzia akceptovaná a dokončená — bez UAT testu (projekt nemá nakonfigurovaný UAT)."


def _last_completion_content(db, version_id) -> str:
    return _director_notes(db, version_id)[-1].content


@pytest.mark.asyncio
async def test_uat_accept_after_successful_deploy_claims_uat(db_session, monkeypatch) -> None:
    """A REAL UAT deploy (ok=True) recorded → uat_accept keeps 'UAT akceptované zákazníkom …'."""
    version_id, state = _seed(db_session, repo_url="https://github.com/x/y", uat_slug="demo")
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)

    async def _deploy(project_slug, uat_slug):
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)
    await orchestrator._release_auto_uat_deploy(db_session, state)  # records {uat_deploy: {ok: True}} + awaiting

    out = await orchestrator.apply_action(db_session, version_id=version_id, action="uat_accept")

    assert out.status == "done"
    assert _last_completion_content(db_session, version_id) == UAT_MSG


@pytest.mark.asyncio
async def test_uat_accept_after_compose_missing_provision_claims_uat(db_session, monkeypatch) -> None:
    """v0.9.0 CR-1: uat_slug SET but compose MISSING now PROVISIONS + deploys (no longer an honest-skip) →
    a REAL deploy is recorded → uat_accept legitimately claims 'UAT akceptované zákazníkom …'."""
    version_id, state = _seed(db_session, repo_url="https://github.com/x/y", uat_slug="gone")
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: False)
    monkeypatch.setattr(orchestrator.uat_provisioner, "provision_uat", lambda *a, **kw: object())

    async def _deploy(project_slug, uat_slug):
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)
    await orchestrator._release_auto_uat_deploy(db_session, state)  # provision + real deploy: {uat_deploy: {ok: True}}

    out = await orchestrator.apply_action(db_session, version_id=version_id, action="uat_accept")

    assert out.status == "done"
    assert _last_completion_content(db_session, version_id) == UAT_MSG


@pytest.mark.asyncio
async def test_uat_accept_uat_slug_set_but_no_deploy_is_honest(db_session) -> None:
    """uat_slug SET but NO uat_deploy ever recorded → honest no-UAT message (proves it keys on the deploy
    OUTCOME, not the slug proxy — the proxy would falsely claim 'UAT akceptované' here)."""
    version_id, _state = _seed(
        db_session, repo_url="https://github.com/x/y", uat_slug="demo", status="awaiting_director"
    )

    out = await orchestrator.apply_action(db_session, version_id=version_id, action="uat_accept")

    assert out.status == "done"
    content = _last_completion_content(db_session, version_id)
    assert content == NO_UAT_MSG
    assert "UAT akceptované" not in content


@pytest.mark.asyncio
async def test_uat_accept_after_failed_deploy_is_honest(db_session, monkeypatch) -> None:
    """A FAILED UAT deploy (ok=False) → uat_accept is honest (no false 'UAT akceptované')."""
    version_id, state = _seed(db_session, repo_url="https://github.com/x/y", uat_slug="demo")
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)

    async def _deploy(project_slug, uat_slug):
        return False, "exit 1: boom"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)
    await orchestrator._release_auto_uat_deploy(db_session, state)  # blocked + {uat_deploy: {ok: False}}

    out = await orchestrator.apply_action(db_session, version_id=version_id, action="uat_accept")

    assert out.status == "done"
    assert "UAT akceptované" not in _last_completion_content(db_session, version_id)


@pytest.mark.asyncio
async def test_uat_accept_without_uat_slug_is_honest(db_session) -> None:
    """uat_slug NULL → completion is HONEST: no false 'UAT akceptované', states it finished without UAT."""
    version_id, _state = _seed(db_session, repo_url="https://github.com/x/y", uat_slug=None, status="awaiting_director")

    out = await orchestrator.apply_action(db_session, version_id=version_id, action="uat_accept")

    assert out.status == "done"
    content = _last_completion_content(db_session, version_id)
    assert content == NO_UAT_MSG
    assert "UAT akceptované" not in content


# ---------------------------------------------------------------------------
# Fast-fix lane UNTOUCHED — _fast_fix_auto_deploy behaviour locked byte-identical (v0.8.1 scope/safety)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_fix_deploy_runs_and_awaits_unchanged(db_session, monkeypatch) -> None:
    """fast_fix: uat_slug set + compose → _run_uat_deploy runs → awaiting 'Nasadené na UAT' (unchanged)."""
    version_id, state = _seed(db_session, repo_url=None, uat_slug="demo", flow_type="fast_fix")
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)

    async def _deploy(project_slug, uat_slug):
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)

    await orchestrator._fast_fix_auto_deploy(db_session, state)

    assert state.status == "awaiting_director"
    assert state.next_action == "Nasadené na UAT — over a akceptuj."
    assert _director_notes(db_session, version_id)[-1].payload == {
        "uat_deploy": {"uat_slug": "demo", "ok": True, "detail": "OK"}
    }


@pytest.mark.asyncio
async def test_fast_fix_deploy_null_slug_keeps_its_own_skip_message(db_session) -> None:
    """fast_fix NULL skip keeps its ORIGINAL message — DISTINCT from the full-flow honest 'Žiadny UAT'
    skip — proving v0.8.1 left the fast-fix path byte-identical."""
    version_id, state = _seed(db_session, repo_url=None, uat_slug=None, flow_type="fast_fix")

    await orchestrator._fast_fix_auto_deploy(db_session, state)

    assert state.status == "awaiting_director"
    assert state.next_action == "Director: over a akceptuj (UAT deploy preskočený — projekt nemá UAT)."
    note = _director_notes(db_session, version_id)[-1]
    assert note.content == "UAT nie je pre projekt nakonfigurované — preskakujem deploy."
    assert note.payload == {"uat_deploy": {"skipped": True}}
    # The fast-fix skip must NOT use the full-flow wording.
    assert "Žiadny UAT nakonfigurovaný" not in state.next_action


# ---------------------------------------------------------------------------
# H2 (CR-2): the engine RE-PROVISIONS a failed/stale UAT render on retry (self-heal) instead of re-`up`-ing a
# render the image can't import — while PRESERVING a working current-iteration UAT (the regression guard).
# ---------------------------------------------------------------------------


def _uat_note(db, version_id, payload, stage: str = "release"):
    return orchestrator._record_message(
        db,
        version_id=version_id,
        stage=stage,
        author="system",
        recipient="director",
        kind="notification",
        content="uat_deploy note",
        payload=payload,
    )


def _verdict(db, version_id, verdict: str = "PASS"):
    """Record a gate_g verdict — this is what moves :func:`_iteration_boundary_seq`."""
    return orchestrator._record_message(
        db,
        version_id=version_id,
        stage="gate_g",
        author="director",
        recipient="auditor",
        kind="verdict",
        content=verdict,
        payload={"verdict": verdict},
    )


def test_prior_uat_deploy_failed_predicate(db_session) -> None:
    """Unit the predicate across every branch: None→False, skipped→False, ok:False→True, ok:True (current
    iteration)→False, ok:True (prior iteration)→True. seq is DB-assigned (Identity), so recording order ⇒
    seq order: a verdict recorded AFTER a deploy note moves the iteration boundary PAST it (→ stale)."""
    R = orchestrator._uat_render_needs_reprovision

    # (a) no deploy ever recorded → nothing to heal.
    v_none, _ = _seed(db_session, repo_url="https://github.com/x/y", uat_slug="demo")
    assert R(db_session, v_none) is False

    # (b) a skipped deploy → nothing to heal.
    v_skip, _ = _seed(db_session, repo_url="https://github.com/x/y", uat_slug="demo")
    _uat_note(db_session, v_skip, {"uat_deploy": {"skipped": True}})
    assert R(db_session, v_skip) is False

    # (c) the latest deploy FAILED (NARROW core) → reprovision.
    v_fail, _ = _seed(db_session, repo_url="https://github.com/x/y", uat_slug="demo")
    _uat_note(db_session, v_fail, {"uat_deploy": {"uat_slug": "demo", "ok": False, "detail": "boom"}})
    assert R(db_session, v_fail) is True

    # (d) ok:True recorded AFTER the boundary verdict (current iteration) → preserve the working UAT.
    v_cur, _ = _seed(db_session, repo_url="https://github.com/x/y", uat_slug="demo")
    _verdict(db_session, v_cur)  # boundary
    _uat_note(db_session, v_cur, {"uat_deploy": {"uat_slug": "demo", "ok": True, "detail": "OK"}})
    assert R(db_session, v_cur) is False

    # (e) ok:True from a PRIOR iteration (a newer verdict moved the boundary past it) → stale → reprovision.
    v_prior, _ = _seed(db_session, repo_url="https://github.com/x/y", uat_slug="demo")
    _uat_note(db_session, v_prior, {"uat_deploy": {"uat_slug": "demo", "ok": True, "detail": "OK"}})
    _verdict(db_session, v_prior)  # new iteration boundary, seq > the deploy note
    assert R(db_session, v_prior) is True


@pytest.mark.asyncio
async def test_full_flow_retry_after_failed_deploy_reprovisions(db_session, monkeypatch) -> None:
    """Full-flow: compose present + a prior FAILED deploy → provision_uat IS called (re-render the broken
    render) → _run_uat_deploy → awaiting_director."""
    version_id, state = _seed(db_session, repo_url="https://github.com/rauschiccsk/nex-demo", uat_slug="demo")
    _uat_note(db_session, version_id, {"uat_deploy": {"uat_slug": "demo", "ok": False, "detail": "boom"}})
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)

    prov = {"called": False}

    def _provision(project_slug, uat_slug, *, version):
        prov["called"] = True
        return object()

    monkeypatch.setattr(orchestrator.uat_provisioner, "provision_uat", _provision)

    async def _deploy(project_slug, uat_slug):
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)

    await orchestrator._release_auto_uat_deploy(db_session, state)

    assert prov["called"] is True, "a failed render must be re-provisioned, not re-`up`-ed verbatim"
    assert state.status == "awaiting_director"
    assert _director_notes(db_session, version_id)[-1].payload == {
        "uat_deploy": {"uat_slug": "demo", "ok": True, "detail": "OK"}
    }


@pytest.mark.asyncio
async def test_full_flow_redeploy_after_success_does_not_reprovision(db_session, monkeypatch) -> None:
    """The preserve-working-UAT regression guard (MANDATORY): compose present + a prior SUCCESSFUL
    current-iteration deploy → provision_uat is NEVER called (redeploy only)."""
    version_id, state = _seed(db_session, repo_url="https://github.com/rauschiccsk/nex-demo", uat_slug="demo")
    _uat_note(db_session, version_id, {"uat_deploy": {"uat_slug": "demo", "ok": True, "detail": "OK"}})
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)

    def _no_provision(*a, **kw):
        raise AssertionError("a working current-iteration UAT must NOT be re-provisioned")

    monkeypatch.setattr(orchestrator.uat_provisioner, "provision_uat", _no_provision)

    async def _deploy(project_slug, uat_slug):
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)

    await orchestrator._release_auto_uat_deploy(db_session, state)

    assert state.status == "awaiting_director"
    assert state.next_action == "Nasadené na UAT — over a akceptuj."


@pytest.mark.asyncio
async def test_fast_fix_retry_after_failed_deploy_reprovisions(db_session, monkeypatch) -> None:
    """Fast-fix mirror: compose present + a prior FAILED deploy → provision_uat IS called before redeploy."""
    version_id, state = _seed(db_session, repo_url=None, uat_slug="demo", flow_type="fast_fix")
    _uat_note(db_session, version_id, {"uat_deploy": {"uat_slug": "demo", "ok": False, "detail": "boom"}})
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)

    prov = {"called": False}

    def _provision(project_slug, uat_slug, *, version):
        prov["called"] = True
        return object()

    monkeypatch.setattr(orchestrator.uat_provisioner, "provision_uat", _provision)

    async def _deploy(project_slug, uat_slug):
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)

    await orchestrator._fast_fix_auto_deploy(db_session, state)

    assert prov["called"] is True
    assert state.status == "awaiting_director"


@pytest.mark.asyncio
async def test_fast_fix_redeploy_after_success_does_not_reprovision(db_session, monkeypatch) -> None:
    """Fast-fix mirror of the regression guard: a prior SUCCESSFUL current-iteration deploy → no re-provision."""
    version_id, state = _seed(db_session, repo_url=None, uat_slug="demo", flow_type="fast_fix")
    _uat_note(db_session, version_id, {"uat_deploy": {"uat_slug": "demo", "ok": True, "detail": "OK"}})
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)

    def _no_provision(*a, **kw):
        raise AssertionError("a working current-iteration fast-fix UAT must NOT be re-provisioned")

    monkeypatch.setattr(orchestrator.uat_provisioner, "provision_uat", _no_provision)

    async def _deploy(project_slug, uat_slug):
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)

    await orchestrator._fast_fix_auto_deploy(db_session, state)

    assert state.status == "awaiting_director"
    assert state.next_action == "Nasadené na UAT — over a akceptuj."


@pytest.mark.asyncio
async def test_fast_fix_provision_failure_blocks(db_session, monkeypatch) -> None:
    """Fast-fix mirror of test_full_flow_uat_provision_failure_blocks: when the H2 self-heal re-provision
    RAISES, the lane settles to blocked (system_error) + records a {ok:False, provisioned:False} note and the
    redeploy NEVER runs (no silent re-`up`). compose present + a prior FAILED deploy arms the re-provision."""
    version_id, state = _seed(db_session, repo_url=None, uat_slug="demo", flow_type="fast_fix")
    _uat_note(db_session, version_id, {"uat_deploy": {"uat_slug": "demo", "ok": False, "detail": "boom"}})
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)

    def _provision(project_slug, uat_slug, *, version):
        raise RuntimeError("source docker-compose.yml not found")

    monkeypatch.setattr(orchestrator.uat_provisioner, "provision_uat", _provision)

    async def _deploy(project_slug, uat_slug):
        raise AssertionError("a provision failure must NOT proceed to deploy")

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)

    await orchestrator._fast_fix_auto_deploy(db_session, state)

    assert state.status == "blocked"
    assert state.block_reason == "system_error"
    assert "UAT provisioning zlyhal" in state.next_action and "not found" in state.next_action
    note = _director_notes(db_session, version_id)[-1]
    assert note.payload["uat_deploy"]["ok"] is False
    assert note.payload["uat_deploy"]["provisioned"] is False


# ---------------------------------------------------------------------------
# CR-R2-2 (#2): the H2 3rd trigger self-heals a broken EXISTING render (a skip / no deploy note but a
# non-importable on-disk DATABASE_URL) — keyed on the H1 validator pair, reused verbatim.
# ---------------------------------------------------------------------------

# A pg8000-only project's correct (driver-carrying) vs broken (bare) rendered DATABASE_URL.
_BROKEN_ENV = "DATABASE_URL=postgresql://u:p@db:5432/d\nSECRET=x\n"
_VALID_ENV = "DATABASE_URL=postgresql+pg8000://u:p@db:5432/d\nSECRET=x\n"


def _arm_existing_render(monkeypatch, tmp_path, env_text: str, *, drivers) -> None:
    """Arrange an on-disk UAT render under a tmp UAT_ROOT + a stubbed source-driver set (H1 reused verbatim)."""
    uat_dir = tmp_path / "uat" / "demo"
    uat_dir.mkdir(parents=True)
    (uat_dir / ".env").write_text(env_text)
    monkeypatch.setattr(orchestrator, "UAT_ROOT", tmp_path / "uat")
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)
    monkeypatch.setattr(orchestrator.uat_provisioner, "detect_sqlalchemy_pg_drivers", lambda p: drivers)


def test_render_needs_reprovision_self_heals_broken_existing_env(db_session, monkeypatch, tmp_path) -> None:
    """3rd trigger: NO deploy note, but the on-disk .env is a bare postgresql:// while the source ships
    pg8000-only (the nex-manager orphan) → the H1 pair FAILs → re-provision (predicate True)."""
    version_id, _ = _seed(db_session, repo_url="https://github.com/x/y", uat_slug="demo")
    _arm_existing_render(monkeypatch, tmp_path, _BROKEN_ENV, drivers={"pg8000"})
    assert orchestrator._uat_render_needs_reprovision(db_session, version_id) is True


def test_render_needs_reprovision_keeps_valid_existing_env(db_session, monkeypatch, tmp_path) -> None:
    """3rd trigger: an existing .env that PASSES H1 (postgresql+pg8000://) → no needless re-provision (False)."""
    version_id, _ = _seed(db_session, repo_url="https://github.com/x/y", uat_slug="demo")
    _arm_existing_render(monkeypatch, tmp_path, _VALID_ENV, drivers={"pg8000"})
    assert orchestrator._uat_render_needs_reprovision(db_session, version_id) is False


def test_render_needs_reprovision_skip_note_unchanged_when_no_env_on_disk(db_session, monkeypatch, tmp_path) -> None:
    """A skip note with NO render on disk → nothing to heal (predicate stays False — note-based branch intact)."""
    version_id, _ = _seed(db_session, repo_url="https://github.com/x/y", uat_slug="demo")
    _uat_note(db_session, version_id, {"uat_deploy": {"skipped": True}})
    # compose exists but there is no .env under the tmp UAT_ROOT → False.
    monkeypatch.setattr(orchestrator, "UAT_ROOT", tmp_path / "uat")
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)
    assert orchestrator._uat_render_needs_reprovision(db_session, version_id) is False


# ---------------------------------------------------------------------------
# CR-R2-1 (#1b): no silent "done without UAT" for a STRUCTURALLY-deployable app (backend+db). The guard is
# applied on BOTH paths to done — uat_accept AND the generic approve→done advance.
# ---------------------------------------------------------------------------

_DEPLOYABLE_COMPOSE = "services:\n  backend:\n    build: ./backend\n  db:\n    image: postgres:16\n"
_BACKEND_ONLY_COMPOSE = "services:\n  backend:\n    build: ./backend\n"


def _seed_with_source(db, src_dir, *, compose, uat_slug="demo", stage="release", status="awaiting_director"):
    """Seed a release-stage version whose project has a source_path (optionally with a docker-compose.yml)."""
    version_id, state = _seed(db, repo_url="https://github.com/x/y", uat_slug=uat_slug, stage=stage, status=status)
    project = db.execute(
        select(Project).join(Version, Version.project_id == Project.id).where(Version.id == version_id)
    ).scalar_one()
    src_dir.mkdir(parents=True, exist_ok=True)
    if compose is not None:
        (src_dir / "docker-compose.yml").write_text(compose)
    project.source_path = str(src_dir)
    db.flush()
    return version_id, state


def test_project_is_deployable_matrix(db_session, tmp_path) -> None:
    """STRUCTURAL deployability = source compose ships BOTH backend + db; everything else → False."""
    D = orchestrator._project_is_deployable

    v_dep, _ = _seed_with_source(db_session, tmp_path / "dep", compose=_DEPLOYABLE_COMPOSE)
    assert D(db_session, v_dep) is True

    v_nodb, _ = _seed_with_source(db_session, tmp_path / "nodb", compose=_BACKEND_ONLY_COMPOSE)
    assert D(db_session, v_nodb) is False

    v_nocompose, _ = _seed_with_source(db_session, tmp_path / "empty", compose=None)
    assert D(db_session, v_nocompose) is False

    v_bad, _ = _seed_with_source(db_session, tmp_path / "bad", compose=": : not yaml : :\n  - [\n")
    assert D(db_session, v_bad) is False

    v_nosrc, _ = _seed(db_session, repo_url="https://github.com/x/y", uat_slug="demo")
    assert D(db_session, v_nosrc) is False  # no source_path


@pytest.mark.asyncio
async def test_uat_accept_blocks_deployable_app_without_real_uat(db_session, tmp_path) -> None:
    """A deployable app (backend+db) with NO successful deploy note → uat_accept FAILS LOUD (no silent done)."""
    version_id, _ = _seed_with_source(db_session, tmp_path / "dep", compose=_DEPLOYABLE_COMPOSE)
    with pytest.raises(orchestrator.OrchestratorError, match="Reálny UAT nebol nasadený"):
        await orchestrator.apply_action(db_session, version_id=version_id, action="uat_accept")


@pytest.mark.asyncio
async def test_uat_accept_completes_non_deployable_lib_project(db_session, tmp_path) -> None:
    """A pure-lib project (backend-only, no db) with no deploy → uat_accept completes normally (honest no-UAT)."""
    version_id, _ = _seed_with_source(db_session, tmp_path / "lib", compose=_BACKEND_ONLY_COMPOSE)
    out = await orchestrator.apply_action(db_session, version_id=version_id, action="uat_accept")
    assert out.status == "done"
    assert _last_completion_content(db_session, version_id) == NO_UAT_MSG


@pytest.mark.asyncio
async def test_uat_accept_deployable_with_real_uat_reaches_done(db_session, monkeypatch, tmp_path) -> None:
    """The happy path on the uat_accept route: a deployable app WITH a real ok:True deploy → done (UAT claimed)."""
    version_id, state = _seed_with_source(db_session, tmp_path / "dep", compose=_DEPLOYABLE_COMPOSE)
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)

    async def _deploy(project_slug, uat_slug):
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)
    await orchestrator._release_auto_uat_deploy(db_session, state)  # records {uat_deploy: {ok: True}}

    out = await orchestrator.apply_action(db_session, version_id=version_id, action="uat_accept")
    assert out.status == "done"
    assert _last_completion_content(db_session, version_id) == UAT_MSG


@pytest.mark.asyncio
async def test_generic_approve_blocks_deployable_app_without_real_uat(db_session, tmp_path) -> None:
    """The SAME guard on the generic approve→done advance (else bypassable): release-stage deployable app
    with no deploy → approve FAILS LOUD."""
    version_id, _ = _seed_with_source(db_session, tmp_path / "dep", compose=_DEPLOYABLE_COMPOSE)
    with pytest.raises(orchestrator.OrchestratorError, match="Reálny UAT nebol nasadený"):
        await orchestrator.apply_action(db_session, version_id=version_id, action="approve")


@pytest.mark.asyncio
async def test_generic_approve_completes_deployable_with_real_uat(db_session, monkeypatch, tmp_path) -> None:
    """The happy path on the generic approve→done advance: a deployable app WITH a real ok:True deploy → done."""
    version_id, state = _seed_with_source(db_session, tmp_path / "dep", compose=_DEPLOYABLE_COMPOSE)
    monkeypatch.setattr(orchestrator, "_uat_compose_exists", lambda slug: True)

    async def _deploy(project_slug, uat_slug):
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _deploy)
    await orchestrator._release_auto_uat_deploy(db_session, state)  # records {uat_deploy: {ok: True}}, awaiting

    out = await orchestrator.apply_action(db_session, version_id=version_id, action="approve")
    assert out.current_stage == "done"
    assert out.status == "done"

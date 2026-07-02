"""CR-V2-014 — Verifikácia phase (the independent Auditor's END verification, replaces v1 gate_g).

Exercised against the REAL v2 branch DB (4-phase + 2-role CHECKs) so the FAIL / verdict / escalation / fix
branches insert against the LIVE constraints — a happy-path-only test would not catch a recipient='director'
or stage='gate_g' write the v2 CHECK rejects.

The Verifikácia round (``_run_verifikacia_round``) is the v2 form of v1 gate_g:

* **Release-acceptance against INTERNAL FIXTURES** — the engine runs the built app via ``_run_release_smoke``
  (an ephemeral isolated ``-p <slug>-smoke`` compose up/down), NEVER a customer instance / uat_provisioner /
  deploy.py (OQ-3/D6 — "Hotovo" = verified, not deployed). The boot + acceptance result is fed to the Auditor.
* **Auditor verdict turn** — the independent Auditor emits ONE ``kind=verdict`` (PASS/FAIL) with the
  adversarial spot-checks + explicit §4 hard-security verification; recorded ``author=auditor`` /
  ``recipient=manazer`` / ``stage=verifikacia`` (valid v2 tokens).
* **PASS** → the dial governs the end sign-off (``plna`` auto-signs-off to Hotovo through the recorded PASS
  verdict — no-silent-done invariant; ``po_kazdej_faze`` stops awaiting_manazer).
* **FAIL** → loop the fix back to the AI Agent (reset done tasks + re-enter Programovanie with the Auditor's
  proposed_fix threaded), bounded by ``AUDITOR_LOOP_MAX``, then escalate to the Manažér.
* A §4 credential leak → the Auditor FAILs it; a parse failure of the verdict → fail-CLOSED (blocked, never
  Hotovo).

``invoke_agent_with_parse_retry`` and ``_run_release_smoke`` are monkeypatched (no live ``claude`` / docker).
"""

import uuid

from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PARTICIPANT_VALUES, STAGE_VALUES, PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_version(db_session, *, project_dial=None):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
        source_path=None,  # library/no-checkout → artifacts live in the DB audit trail
        miera_autonomie=project_dial,
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


def _seed_verifikacia(
    db_session, version_id, *, build_dial=None, iteration=0, is_regate=False, flow_type="new_version"
):
    state = PipelineState(
        version_id=version_id,
        flow_type=flow_type,
        current_stage="verifikacia",
        current_actor="auditor",
        status="agent_working",
        next_action="working",
        miera_autonomie=build_dial,
        iteration=iteration,
        is_regate=is_regate,
    )
    db_session.add(state)
    db_session.flush()
    return state


def _seed_done_tasks(db_session, version, project, titles):
    """Seed ONE epic + ONE feat + a Task per title, all ``done`` (a build that completed Programovanie)."""
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="Foundation", status="done")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="Schema", status="done")
    db_session.add(feat)
    db_session.flush()
    tasks = []
    for i, title in enumerate(titles, start=1):
        t = Task(feat_id=feat.id, number=i, title=title, task_type="backend", status="done")
        db_session.add(t)
        tasks.append(t)
    db_session.flush()
    return epic, feat, tasks


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


def _tasks(db_session, version_id):
    return (
        db_session.execute(
            select(Task)
            .join(Feat, Feat.id == Task.feat_id)
            .join(Epic, Epic.id == Feat.epic_id)
            .where(Epic.version_id == version_id)
        )
        .scalars()
        .all()
    )


# ── stubs ─────────────────────────────────────────────────────────────────────


def _verdict_pass(findings=None):
    return PipelineStatusBlock(
        stage="verifikacia",
        kind="verdict",
        summary="Verzia overená — acceptance + spot-checky + §4 čisté.",
        awaiting="manazer",
        verdict=True,
        findings=findings or [],
    )


def _verdict_fail(findings, proposed_fix="Oprav výpočet DPH pri reverse-charge a doplň test."):
    return PipelineStatusBlock(
        stage="verifikacia",
        kind="verdict",
        summary="Verifikácia FAIL.",
        awaiting="manazer",
        verdict=False,
        findings=findings,
        proposed_fix=proposed_fix,
    )


def _stub_auditor(monkeypatch, audit_block):
    """Make ``invoke_agent_with_parse_retry`` answer the Auditor verifikacia turn with *audit_block*, capturing
    the prompt. The real ``invoke_agent`` would record the verdict message; the round runner ALSO records the
    canonical verdict message, so the stub returns the block WITHOUT recording (avoids a duplicate)."""
    captured = {}

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        captured["role"] = role
        captured["stage"] = stage
        captured["prompt"] = prompt
        captured["recipient"] = _kw.get("recipient")
        return audit_block

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return captured


def _stub_smoke(monkeypatch, *, boot_ok=True, acc=(True, "release acceptance PASS — 12 assertions", False)):
    """Stub ``_run_release_smoke`` (no docker). Returns ((boot_ok, detail), acceptance|None) like the real one
    (acceptance is None when boot failed). Captures the (slug, version) it was called with."""
    seen = {}

    async def _fake(project_slug, version_label, coverage_req=(0, 0)):
        seen["slug"] = project_slug
        seen["version"] = version_label
        seen["coverage_req"] = coverage_req
        if not boot_ok:
            return (False, "up exit 1: boot failed"), None
        return (True, "app booted + responds"), acc

    monkeypatch.setattr(orchestrator, "_run_release_smoke", _fake)
    return seen


def _ban_deploy_calls(monkeypatch):
    """Belt-and-suspenders for "release smoke runs WITHOUT provisioning any customer instance": blow up if the
    Verifikácia path ever calls a deploy / uat_provisioner entry point."""

    def _boom(*_a, **_k):
        raise AssertionError("Verifikácia must NOT touch a customer instance (uat_provisioner/deploy)")

    monkeypatch.setattr(orchestrator.uat_provisioner, "provision_uat", _boom)
    if hasattr(orchestrator, "_run_uat_deploy"):

        async def _aboom(*_a, **_k):
            raise AssertionError("Verifikácia must NOT call _run_uat_deploy")

        monkeypatch.setattr(orchestrator, "_run_uat_deploy", _aboom)


# ── the verdict brief (DESIGN-BEARING) ────────────────────────────────────────


async def test_verifikacia_brief_is_release_acceptance_internal_fixtures_security(db_session, monkeypatch):
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_verifikacia(db_session, version.id)
    cap = _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch)
    _ban_deploy_calls(monkeypatch)
    await orchestrator.run_dispatch(db_session, version.id)
    p = cap["prompt"]
    assert cap["role"] == orchestrator.AUDITOR_ROLE and cap["stage"] == "verifikacia"
    assert cap["recipient"] == "manazer"
    assert "RELEASE-ACCEPTANCE" in p  # behavioural pillar
    assert "INTERNÝM FIXTÚRAM" in p  # internal fixtures, not a customer instance
    assert "READ + RUN-ONLY" in p  # independence
    assert "§4 HARD-SECURITY" in p  # explicit §4 verification
    assert "REFUTUJ, NEPOTVRDZUJ" in p  # CR-V2-053: refute-don't-confirm
    assert "NEGATÍVNE / BEZPEČNOSTNÉ OVERENIE" in p  # CR-V2-053: unconditional negative/safety mandate
    assert "kind=verdict" in p
    # the engine smoke result was fed into the brief
    assert "Engine release smoke" in p and "interné fixtúry" in p


async def test_verifikacia_brief_depth_is_dial_independent(db_session, monkeypatch):
    # CR-V2-053: the END verification depth is FIXED — the same deep, adversarial, refute-don't-confirm brief
    # regardless of the Miera autonómie dial (the old down-scaling at low autonomy is removed). The dial
    # governs WHERE the build stops for approval, NOT how hard the release gate is checked.
    hi, _ = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, hi.id)
    cap_hi = _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch)
    await orchestrator.run_dispatch(db_session, hi.id)

    lo, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_verifikacia(db_session, lo.id)
    cap_lo = _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch)
    await orchestrator.run_dispatch(db_session, lo.id)

    # identical brief at both dial ends (no dial-conditional depth text); both carry the refute + negative mandate
    assert cap_hi["prompt"] == cap_lo["prompt"]
    for needle in ("REFUTUJ, NEPOTVRDZUJ", "NEGATÍVNE / BEZPEČNOSTNÉ OVERENIE", "rovnaká PLNÁ hĺbka VŽDY"):
        assert needle in cap_hi["prompt"]
    # the removed dial-conditional wording is gone
    assert "DÔKLADNÚ" not in cap_hi["prompt"] and "ZAMERANÚ" not in cap_lo["prompt"]


async def test_verifikacia_brief_enumerates_declared_coverage(db_session, monkeypatch):
    # CR-V2-053: the Auditor brief names the EXACT declared safety properties (risky ops) to run + reject, so
    # the negative-test mandate is concrete, not abstract.
    version, _ = _make_version(db_session, project_dial="plna")
    _rec_navrh_gate_report(
        db_session,
        version.id,
        flagship_features=["Peppol export"],
        safety_properties=[{"name": "read_only blocks writes", "risky_op": "cat x > y under read_only"}],
    )
    _seed_verifikacia(db_session, version.id)
    cap = _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch)
    await orchestrator.run_dispatch(db_session, version.id)
    p = cap["prompt"]
    assert "Deklarované pokrytie z Návrhu" in p
    assert "read_only blocks writes" in p and "cat x > y under read_only" in p
    assert "Peppol export" in p


# ── PASS: the dial governs the end sign-off ───────────────────────────────────


async def test_pass_new_version_stops_for_signoff_even_at_plna(db_session, monkeypatch):
    # A (Director 2026-06-30): a new_version PASS STOPS at the Verifikácia end for the Manažér's final
    # sign-off ('schvalit' → Hotovo), even at plná — NEVER an auto-Hotovo. The PASS is on record
    # (no-silent-done still holds); Hotovo now needs the Manažér's explicit confirmation (mandatory gate).
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id)
    _stub_auditor(monkeypatch, _verdict_pass(findings=["pozn.: zváž rate-limit"]))
    _stub_smoke(monkeypatch)
    _ban_deploy_calls(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "verifikacia" and state.status == "awaiting_manazer"
    assert orchestrator._verifikacia_passed(db_session, version.id) is True
    verdicts = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"]
    assert verdicts[-1].author == "auditor" and verdicts[-1].recipient == "manazer"
    assert verdicts[-1].stage == "verifikacia" and verdicts[-1].payload["verdict"] == "PASS"


async def test_pass_fast_fix_auto_signs_off_to_hotovo(db_session, monkeypatch):
    # fast_fix keeps its zero-approval lane: a PASS auto-signs-off to Hotovo (terminal) at plná THROUGH the
    # recorded PASS verdict (no-silent-done invariant). Deploy is OUT — Hotovo means verified, not deployed.
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id, flow_type="fast_fix")
    _stub_auditor(monkeypatch, _verdict_pass(findings=["pozn.: zváž rate-limit"]))
    _stub_smoke(monkeypatch)
    _ban_deploy_calls(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "done" and state.status == "done"
    assert "Nasadenie je samostatná akcia" in state.next_action
    assert orchestrator._verifikacia_passed(db_session, version.id) is True


async def test_pass_under_stopping_dial_awaits_manazer_sign_off(db_session, monkeypatch):
    # po_kazdej_faze + PASS → the dial stops the end schvaľovací bod; the Manažér signs off (schvalit → Hotovo).
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_verifikacia(db_session, version.id)
    _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "verifikacia" and state.status == "awaiting_manazer"
    assert "Verifikácia PASS" in state.next_action
    assert orchestrator._verifikacia_passed(db_session, version.id) is True
    # NOT yet Hotovo — the PASS-then-sign-off split
    assert state.status != "done"


# ── FAIL: the bounded fix↔re-verify loop (THE GATE) ───────────────────────────


async def test_fail_loops_targeted_fix_and_gates_new_version(db_session, monkeypatch):
    # A+B (Director 2026-06-30): an injected behavioural FAIL → the build does NOT reach Hotovo; it loops a
    # TARGETED fix back to the AI Agent (re-enter Programovanie). B: the already-done plan tasks STAY done and
    # ONE fix task is materialized (NO whole-build reset). A: a new_version STOPS (paused) for the Manažér to
    # confirm the fix re-run ('Pokračovať') — never an unattended auto re-dispatch. Even at plná.
    version, project = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id, iteration=0)
    _, _, done_tasks = _seed_done_tasks(db_session, version, project, ["T1", "T2"])
    fail = _verdict_fail(["DPH pri reverse-charge sa počíta zle (acceptance 2/12 FAIL)."])
    _stub_auditor(monkeypatch, fail)
    _stub_smoke(monkeypatch, acc=(False, "release_smoke_test.sh exit 1: 2 of 12 failed", False))
    _ban_deploy_calls(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    # re-entered Programovanie (NOT Hotovo), counter bumped — but GATED for the Manažér (paused, not auto)
    assert state.current_stage == "programovanie"
    assert state.iteration == 1
    assert state.status == "paused"  # A: mandatory gate — Manažér confirms via 'Pokračovať'
    # B: the original plan tasks STAY done (no whole-build re-run); a single targeted fix task is added (todo)
    all_tasks = _tasks(db_session, version.id)
    assert all(t.status == "done" for t in all_tasks if t.id in {dt.id for dt in done_tasks})
    fix_tasks = [t for t in all_tasks if t.id not in {dt.id for dt in done_tasks}]
    assert len(fix_tasks) == 1 and fix_tasks[0].status == "todo"
    # the FAIL verdict landed with VALID v2 tokens (the live CHECK accepted it)
    verdicts = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"]
    assert verdicts[-1].author == "auditor" and verdicts[-1].recipient == "manazer"
    assert verdicts[-1].payload["verdict"] == "FAIL" and verdicts[-1].payload["findings"]
    # the fix scope is readable for the re-run brief
    scope = orchestrator._latest_verifikacia_fix_scope(db_session, version.id)
    assert scope is not None and "DPH" in scope and "Verifikácia FAIL" in scope
    # NOT yet verified
    assert orchestrator._verifikacia_passed(db_session, version.id) is False


async def test_fail_then_fix_reaches_hotovo_fast_fix(db_session, monkeypatch):
    # THE FULL LOOP on the fast_fix zero-approval lane: FAIL → AI Agent fixes (Programovanie re-run, stubbed)
    # → Auditor re-verifies PASS → reaches Hotovo, auto-chained (a new_version GATES each transition for the
    # Manažér — A; fast_fix is the auto path that drives the loop end-to-end without stops).
    version, project = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id, iteration=0, flow_type="fast_fix")
    _seed_done_tasks(db_session, version, project, ["T1"])
    # round 1: FAIL → loop to Programovanie
    _stub_auditor(monkeypatch, _verdict_fail(["behaviorálne zlyhanie"]))
    _stub_smoke(monkeypatch, acc=(False, "1 of 5 failed", False))
    _ban_deploy_calls(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "programovanie" and state.iteration == 1

    # Simulate the AI Agent fixing (the Programovanie round is its own CR; here jump the state back to a
    # settled Verifikácia as the auto-chain would after the re-run completes), then re-verify PASS.
    state.current_stage = "verifikacia"
    state.current_actor = "auditor"
    state.status = "agent_working"
    db_session.flush()
    _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch, acc=(True, "5 assertions", False))
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage == "done" and state.status == "done"
    assert orchestrator._verifikacia_passed(db_session, version.id) is True


async def test_fail_at_loop_max_escalates_to_manazer(db_session, monkeypatch):
    # The bounded loop: a still-failing build at the AUDITOR_LOOP_MAX-th round STOPS + escalates to the Manažér
    # (blocked, a visible system→manazer note) — never an infinite loop, never a silent done.
    version, project = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id, iteration=orchestrator.AUDITOR_LOOP_MAX)
    _seed_done_tasks(db_session, version, project, ["T1"])
    _stub_auditor(monkeypatch, _verdict_fail(["stále zlyháva"]))
    _stub_smoke(monkeypatch, acc=(False, "still failing", False))
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked" and state.block_reason == "agent_error"
    assert state.current_stage == "verifikacia"  # did NOT loop back — escalated
    assert f"{orchestrator.AUDITOR_LOOP_MAX}" in state.next_action and "Manažér" in state.next_action
    notes = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("auditor_loop_exhausted")]
    assert notes and notes[-1].author == "system" and notes[-1].recipient == "manazer"
    assert orchestrator._verifikacia_passed(db_session, version.id) is False


# ── §4 credential leak → FAIL ─────────────────────────────────────────────────


async def test_credential_leak_is_flagged_as_fail(db_session, monkeypatch):
    # A §4 hard-security failure (credential in code) is a FAIL verdict — it must NOT reach Hotovo, even at plná.
    version, project = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id)
    _seed_done_tasks(db_session, version, project, ["T1"])
    leak = _verdict_fail(
        ["§4 PORUŠENIE: hardkódovaný DB heslo v backend/config.py:12 — credential v zdrojáku."],
        proposed_fix="Presuň heslo do .env a načítaj cez env var; nikdy v zdrojáku.",
    )
    _stub_auditor(monkeypatch, leak)
    _stub_smoke(monkeypatch)
    _ban_deploy_calls(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage != "done", "a §4 credential leak must not reach Hotovo"
    verdicts = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"]
    assert verdicts[-1].payload["verdict"] == "FAIL"
    assert any("§4" in f for f in verdicts[-1].payload["findings"])
    assert orchestrator._verifikacia_passed(db_session, version.id) is False


# ── fail-closed on a bad verdict ──────────────────────────────────────────────


async def test_absent_verdict_is_fail_closed(db_session, monkeypatch):
    # A verdict block WITHOUT an explicit verdict=true is treated as FAIL (mirrors _verifikacia_passed).
    version, project = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id)
    _seed_done_tasks(db_session, version, project, ["T1"])
    no_verdict = PipelineStatusBlock(
        stage="verifikacia", kind="verdict", summary="nejasné", awaiting="manazer", findings=["?"]
    )  # verdict defaults to None → not True → FAIL
    _stub_auditor(monkeypatch, no_verdict)
    _stub_smoke(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.current_stage != "done"  # did NOT auto-sign-off
    verdicts = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"]
    assert verdicts[-1].payload["verdict"] == "FAIL"


async def test_parse_failure_is_fail_closed_blocked(db_session, monkeypatch):
    # Verifikácia IS the release gate: an unparseable verdict must NEVER reach Hotovo → blocked, visible note,
    # tokens metered. (Unlike the upfront review's fail-OPEN early net.)
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id)
    pf = ParseFailure(
        "auditor returned no parseable verdict", usage={"input_tokens": 7, "output_tokens": 2, "model": "m"}
    )
    _stub_auditor(monkeypatch, pf)
    _stub_smoke(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    assert state.status == "blocked" and state.block_reason == "agent_error"
    assert state.current_stage == "verifikacia"
    assert orchestrator._verifikacia_passed(db_session, version.id) is False
    # the failure is visible (system→manazer) + the tokens are metered (NOT a director note)
    notes = [
        m
        for m in _msgs(db_session, version.id)
        if m.author == "system" and m.recipient == "manazer" and "nepodarilo spracovať" in m.content
    ]
    assert notes and notes[-1].payload and notes[-1].payload.get("usage")


# ── release smoke runs WITHOUT provisioning a customer instance ────────────────


async def test_release_smoke_runs_against_internal_fixtures_no_customer(db_session, monkeypatch):
    # The CR invariant: the Verifikácia path runs the smoke against INTERNAL FIXTURES — it never provisions /
    # deploys to a customer instance. _ban_deploy_calls asserts no uat_provisioner/deploy is reached; the smoke
    # is called with the project slug + version (the ephemeral -p <slug>-smoke stack, asserted by the stub).
    version, project = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_verifikacia(db_session, version.id)
    _stub_auditor(monkeypatch, _verdict_pass())
    seen = _stub_smoke(monkeypatch)
    _ban_deploy_calls(monkeypatch)
    await orchestrator.run_dispatch(db_session, version.id)
    assert seen["slug"] == project.slug  # smoke ran against the project's own compose (internal fixture)
    assert seen["version"] == version.version_number
    # the smoke outcome is recorded as a system→manazer note (durable Verifikácia artifact), valid v2 tokens
    smoke_notes = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("smoke")]
    assert smoke_notes and smoke_notes[-1].author == "system" and smoke_notes[-1].recipient == "manazer"
    assert smoke_notes[-1].stage == "verifikacia"


def test_no_uat_provisioner_or_deploy_in_verifikacia_source():
    # Grep-assert (CR verification): no uat_provisioner / deploy.py call reachable from the Verifikácia path
    # functions. The smoke uses an ephemeral -p <slug>-smoke compose, never a customer instance.
    import ast
    import inspect

    src = inspect.getsource(orchestrator._run_verifikacia_round)
    src += inspect.getsource(orchestrator._settle_verifikacia_verdict)
    src += inspect.getsource(orchestrator._verifikacia_directive)
    tree = ast.parse("\n".join(line for line in src.splitlines()))
    banned = {"provision_uat", "_run_uat_deploy", "_fast_fix_auto_deploy", "_release_auto_uat_deploy"}
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            called.add(name)
    assert not (called & banned), f"Verifikácia path must not call {called & banned}"


# ── the manual verdict path (apply_action) matches the autonomous one ──────────


async def test_manual_verdict_fail_creates_targeted_fix_and_gates(db_session, monkeypatch):
    # The Manažér's manual verdict (apply_action) shares _settle_verifikacia_verdict with the autonomous round:
    # a manual FAIL on a new_version creates ONE targeted fix task (done stays done — B) and GATES the re-run
    # (paused — A), the same downstream effect as the autonomous path (no divergence).
    version, project = _make_version(db_session, project_dial="po_kazdej_faze")
    state = _seed_verifikacia(db_session, version.id, iteration=0)
    state.status = "awaiting_manazer"  # settled at the Verifikácia stop, the Manažér acts
    db_session.flush()
    _, _, done_tasks = _seed_done_tasks(db_session, version, project, ["T1"])
    new_state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="verdict", payload={"verdict": "FAIL"}
    )
    assert new_state.current_stage == "programovanie" and new_state.iteration == 1
    assert new_state.status == "paused"  # A: gated for the Manažér (Pokračovať resumes the fix)
    # B: the original plan task STAYS done; ONE targeted fix task is added (todo) — no whole-build reset.
    all_tasks = _tasks(db_session, version.id)
    assert all(t.status == "done" for t in all_tasks if t.id in {dt.id for dt in done_tasks})
    fix_tasks = [t for t in all_tasks if t.id not in {dt.id for dt in done_tasks}]
    assert len(fix_tasks) == 1 and fix_tasks[0].status == "todo"
    verdicts = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"]
    assert verdicts[-1].author == "auditor" and verdicts[-1].payload["verdict"] == "FAIL"


async def test_manual_verdict_pass_settles_for_sign_off(db_session, monkeypatch):
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    state = _seed_verifikacia(db_session, version.id)
    state.status = "awaiting_manazer"
    db_session.flush()
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, st: None)
    new_state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="verdict", payload={"verdict": "PASS"}
    )
    assert new_state.status == "awaiting_manazer" and "Verifikácia PASS" in new_state.next_action
    assert orchestrator._verifikacia_passed(db_session, version.id) is True


# ── the auto_chain bound includes AUDITOR_LOOP_MAX (R-AUTOCHAIN finalized) ─────


def test_auto_chain_bound_budgets_auditor_loop_max(db_session):
    version, _ = _make_version(db_session)
    bound = orchestrator.auto_chain_limit(db_session, version.id)
    # len(STAGE_ORDER) monotonic advance + 2 phase steps per Auditor FAIL round, up to AUDITOR_LOOP_MAX rounds.
    assert bound == len(orchestrator.STAGE_ORDER) + 2 * orchestrator.AUDITOR_LOOP_MAX
    # a full 5-round Auditor loop (Verifikácia FAIL → Programovanie → Verifikácia, ×AUDITOR_LOOP_MAX) plus the
    # monotonic advance does NOT exceed the bound → it never mis-trips the runner backstop.
    monotonic = len(orchestrator.STAGE_ORDER)
    full_loop = 2 * orchestrator.AUDITOR_LOOP_MAX
    assert monotonic + full_loop <= bound


# ── LANDMINE belt-and-suspenders: only valid v2 tokens in the live path ────────


async def test_no_invalid_v2_tokens_written_anywhere_in_path(db_session, monkeypatch):
    # The live Verifikácia FAIL path writes ONLY valid v2 participant/stage tokens (auditor/system/manazer;
    # verifikacia) — never director/coordinator/gate_e/gate_g/build/release. Exercises the FAIL branch (the
    # one most likely to leak a v1 token) against the live DB CHECK.
    version, project = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id, iteration=0)
    _seed_done_tasks(db_session, version, project, ["T1"])
    _stub_auditor(monkeypatch, _verdict_fail(["x"]))
    _stub_smoke(monkeypatch, acc=(False, "fail", False))
    await orchestrator.run_dispatch(db_session, version.id)
    for m in _msgs(db_session, version.id):
        assert m.author in PARTICIPANT_VALUES, m.author
        assert m.recipient in PARTICIPANT_VALUES, m.recipient
        assert m.stage in STAGE_VALUES, m.stage


# ── CR-V2-050: fail-closed hard-gate — the runtime floor OVERRIDES the Auditor LLM verdict ─────────────
# The NEX Agents dogfood defect: the Auditor emitted PASS while the release evidence was red. DONE must be
# reality (a green smoke/acceptance), never a self-reported string. A red boot smoke, or an acceptance leg that
# RAN but did not pass, floors the verdict to FAIL regardless of what the Auditor says — autonomous AND manual.


async def test_red_acceptance_floors_auditor_pass_to_fail(db_session, monkeypatch):
    # THE core dogfood: Auditor says PASS, but the acceptance leg RAN and FAILED → the engine floors it to FAIL.
    # A red smoke can NEVER coexist with a green gate. Loops the targeted fix (does NOT reach Hotovo).
    version, project = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id, iteration=0)
    _, _, done_tasks = _seed_done_tasks(db_session, version, project, ["T1"])
    _stub_auditor(monkeypatch, _verdict_pass(findings=["vyzerá to dobre"]))  # LLM PASS — over-claim
    _stub_smoke(monkeypatch, acc=(False, "release_smoke_test.sh exit 1: 2 of 12 failed", False))  # red floor
    _ban_deploy_calls(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    # the recorded verdict is FAIL with the engine-override marker (audit trail), NOT the LLM's PASS
    verdicts = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"]
    assert verdicts[-1].payload["verdict"] == "FAIL"
    assert verdicts[-1].payload.get("engine_override") == "runtime_floor_red"
    assert any("ENGINE OVERRIDE" in f for f in verdicts[-1].payload["findings"])
    # did NOT reach Hotovo — looped the targeted fix back to Programovanie (paused for the Manažér on new_version)
    assert state.current_stage == "programovanie" and state.iteration == 1
    assert orchestrator._verifikacia_passed(db_session, version.id) is False
    # the override is a readable fix scope for the re-run brief (fix loop is not left without a brief)
    assert orchestrator._latest_verifikacia_fix_scope(db_session, version.id) is not None
    fix_tasks = [t for t in _tasks(db_session, version.id) if t.id not in {dt.id for dt in done_tasks}]
    assert len(fix_tasks) == 1 and fix_tasks[0].status == "todo"


async def test_red_boot_floors_auditor_pass_to_fail(db_session, monkeypatch):
    # Boot smoke FAILED (app did not even come up) but the Auditor said PASS → floored to FAIL. Acceptance is
    # None when boot fails; the floor is red on the boot leg alone.
    version, project = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id, iteration=0)
    _seed_done_tasks(db_session, version, project, ["T1"])
    _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch, boot_ok=False)  # boot red → acceptance None
    _ban_deploy_calls(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    verdicts = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"]
    assert verdicts[-1].payload["verdict"] == "FAIL"
    assert verdicts[-1].payload.get("engine_override") == "runtime_floor_red"
    assert state.current_stage != "done"
    assert orchestrator._verifikacia_passed(db_session, version.id) is False


async def test_skip_acceptance_does_not_floor_pass(db_session, monkeypatch):
    # A SKIP is NOT red: an acceptance leg that did not run (no coverage) does not floor a PASS under CR-050 —
    # the floor is for RAN-but-FAILED only. (CR-051 will separately turn missing coverage into a FAIL.) This
    # guards the floor against over-blocking a genuine boot-green PASS.
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_verifikacia(db_session, version.id)
    _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch, acc=(False, "acceptance skipped — no fixtures", True))  # skipped=True → not red
    _ban_deploy_calls(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    verdicts = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"]
    assert verdicts[-1].payload["verdict"] == "PASS"
    assert "engine_override" not in verdicts[-1].payload
    assert state.status == "awaiting_manazer"  # settled for the end sign-off (no floor)
    assert orchestrator._verifikacia_passed(db_session, version.id) is True


async def test_green_smoke_preserves_auditor_pass(db_session, monkeypatch):
    # Regression guard: a genuinely green smoke + acceptance leaves the Auditor's PASS intact (the floor only
    # bites on red evidence — it does not turn every build into a FAIL).
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _seed_verifikacia(db_session, version.id)
    _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch)  # default: boot ok, acc=(True, ..., False) — green
    _ban_deploy_calls(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    verdicts = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"]
    assert verdicts[-1].payload["verdict"] == "PASS"
    assert "engine_override" not in verdicts[-1].payload
    assert state.status == "awaiting_manazer"


async def test_manual_pass_override_cannot_cross_red_floor(db_session, monkeypatch):
    # The manual path (apply_action verdict): a Manažér PASS-override cannot cross a red floor either. The floor
    # is recomputed from the recorded release evidence; the EFFECTIVE verdict recorded is FAIL (so the fix loop's
    # _latest_verifikacia_fix_scope can never read a PASS while the settle takes the FAIL branch).
    version, project = _make_version(db_session, project_dial="po_kazdej_faze")
    state = _seed_verifikacia(db_session, version.id, iteration=0)
    state.status = "awaiting_manazer"
    db_session.flush()
    _, _, done_tasks = _seed_done_tasks(db_session, version, project, ["T1"])
    # record the canonical release-evidence the autonomous round would have written: boot green, acceptance RED
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="verifikacia",
        author="system",
        recipient="manazer",
        kind="notification",
        content="Release smoke — boot PASS",
        payload={"phase": "verifikacia", "smoke": {"pass": True, "detail": "booted"}},
    )
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="verifikacia",
        author="system",
        recipient="manazer",
        kind="notification",
        content="Release acceptance — FAIL",
        payload={
            "phase": "verifikacia",
            "release_acceptance": {"pass": False, "detail": "2 of 12 failed", "skipped": False},
        },
    )
    db_session.flush()
    new_state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="verdict", payload={"verdict": "PASS"}
    )
    # the manual PASS was floored to FAIL → the fix loop, not the sign-off
    verdicts = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"]
    assert verdicts[-1].content == "FAIL" and verdicts[-1].payload["verdict"] == "FAIL"
    assert verdicts[-1].payload.get("engine_override") == "runtime_floor_red"
    assert new_state.current_stage == "programovanie" and new_state.status == "paused"
    assert orchestrator._verifikacia_passed(db_session, version.id) is False
    fix_tasks = [t for t in _tasks(db_session, version.id) if t.id not in {dt.id for dt in done_tasks}]
    assert len(fix_tasks) == 1 and fix_tasks[0].status == "todo"


async def test_latest_runtime_floor_red_reads_recorded_evidence(db_session):
    # Unit-level guard on the helper the manual path uses: it reads the LATEST recorded smoke/acceptance and
    # returns red iff boot failed OR acceptance ran-but-failed; a SKIP or no-evidence is not red.
    version, _ = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)
    # no evidence on record → floor clear
    assert orchestrator._latest_runtime_floor_red(db_session, version.id) is False

    def _rec(payload):
        orchestrator._record_message(
            db_session,
            version_id=version.id,
            stage="verifikacia",
            author="system",
            recipient="manazer",
            kind="notification",
            content="evidence",
            payload={"phase": "verifikacia", **payload},
        )
        db_session.flush()

    _rec({"smoke": {"pass": True, "detail": "ok"}})
    _rec({"release_acceptance": {"pass": True, "detail": "12/12", "skipped": False}})
    assert orchestrator._latest_runtime_floor_red(db_session, version.id) is False  # green
    _rec({"release_acceptance": {"pass": False, "detail": "no fixtures", "skipped": True}})
    assert orchestrator._latest_runtime_floor_red(db_session, version.id) is False  # SKIP is not red
    _rec({"release_acceptance": {"pass": False, "detail": "2/12 failed", "skipped": False}})
    assert orchestrator._latest_runtime_floor_red(db_session, version.id) is True  # ran-but-failed → red
    _rec({"smoke": {"pass": False, "detail": "boot exit 1"}})
    assert orchestrator._latest_runtime_floor_red(db_session, version.id) is True  # boot red → red


# ── CR-V2-051: spec-derived risk-floored oracle — the declared coverage feeds the acceptance floor ─────


def _rec_navrh_gate_report(db_session, version_id, *, flagship_features=None, safety_properties=None):
    """Record the Návrh gate_report the AI Agent closes the design with, carrying the declared flagship
    features + safety properties (CR-V2-052 populates these; here seeded directly to exercise the reader)."""
    payload = {"phase": "navrh", "plan": {"epics": []}}
    if flagship_features is not None:
        payload["flagship_features"] = flagship_features
    if safety_properties is not None:
        payload["safety_properties"] = safety_properties
    orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage="navrh",
        author="ai_agent",
        recipient="manazer",
        kind="gate_report",
        content="Návrh hotový",
        payload=payload,
    )
    db_session.flush()


async def test_declared_release_coverage_reads_navrh_declaration(db_session):
    version, _ = _make_version(db_session)
    # no design on record → (0, 0), the graceful degradation to the anti-empty floor
    assert orchestrator._declared_release_coverage(db_session, version.id) == (0, 0)
    _rec_navrh_gate_report(
        db_session,
        version.id,
        flagship_features=["PDF→Peppol export", "supplier auto-match"],
        safety_properties=[{"name": "read_only blocks writes", "risky_op": "cat x > y"}],
    )
    assert orchestrator._declared_release_coverage(db_session, version.id) == (2, 1)


async def test_declared_coverage_flows_into_release_smoke(db_session, monkeypatch):
    # The engine reads the Návrh declaration and threads the coverage requirement into the release smoke — so
    # the oracle floors the acceptance against what the design promised (2 flagship features, 1 safety property).
    version, _ = _make_version(db_session, project_dial="po_kazdej_faze")
    _rec_navrh_gate_report(
        db_session,
        version.id,
        flagship_features=["export", "match"],
        safety_properties=[{"name": "authz", "risky_op": "cross-tenant read"}],
    )
    _seed_verifikacia(db_session, version.id)
    _stub_auditor(monkeypatch, _verdict_pass())
    seen = _stub_smoke(monkeypatch)
    _ban_deploy_calls(monkeypatch)
    await orchestrator.run_dispatch(db_session, version.id)
    assert seen["coverage_req"] == (2, 1)  # declared coverage reached the oracle

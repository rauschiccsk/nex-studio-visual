"""DEV-241 — the live preview runs on the libraries its frontend has NOW, not on the ones it started with.

27.10.2026, NEX Knowledge 0.1.0 in the Vizuál step: the AI Agent raised nex-shared to v0.24.0 (cards with an inner
spacing, DEV-240) — package.json, package-lock.json and node_modules were new — and the preview kept serving the old
card. Vite bundles the libraries once, when it starts (its cache lives in the container's tmpfs); the cockpit started
the preview at the beginning of the round and, finding it running, reused it, and „Spustiť náhľad znova“ leaves a
running preview alone. The Manažér saw the old state with no way to refresh it. Director: „Súhlasím“.

Pinned: a preview carries the fingerprint of the libraries it was started with (its package-lock.json); one started
with other libraries — or before the fingerprint existed — is built anew wherever the cockpit starts or looks at it:
the round, its end, the state check of the Riadiace centrum and „Spustiť náhľad znova“. A rebuild that fails reads as
a preview that does not run. Two looks at once rebuild it once.
"""

from __future__ import annotations

import hashlib
import subprocess
import threading
import time

import pytest

from backend.services import orchestrator, vizual_sandbox
from tests.test_orchestrator_v2_vizual import _gate_report, _make_version, _seed_state, _stub_invoke_capture

LABEL = "nex-studio.vizual.dependencies"


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


LOCK_2 = _fingerprint(b'{"lock": 2}')
NEW = _fingerprint(b'{"lock": "new"}')


# ── the fingerprint ─────────────────────────────────────────────────────────────────────────────────────────────


def test_the_fingerprint_is_the_lock_file(tmp_path) -> None:
    (tmp_path / "package-lock.json").write_bytes(b'{"lock": 2}')
    (tmp_path / "package.json").write_bytes(b'{"name": "x"}')

    assert vizual_sandbox.dependencies_fingerprint(tmp_path) == _fingerprint(b'{"lock": 2}')


def test_without_a_lock_file_the_fingerprint_is_package_json(tmp_path) -> None:
    (tmp_path / "package.json").write_bytes(b'{"name": "x"}')

    assert vizual_sandbox.dependencies_fingerprint(tmp_path) == _fingerprint(b'{"name": "x"}')


def test_a_frontend_without_either_has_an_empty_fingerprint(tmp_path) -> None:
    assert vizual_sandbox.dependencies_fingerprint(tmp_path) == ""


def test_the_preview_is_started_with_the_fingerprint_of_its_libraries(tmp_path) -> None:
    (tmp_path / "package-lock.json").write_bytes(b'{"lock": 2}')

    argv = vizual_sandbox.build_run_argv(slug="nex-knowledge", frontend_host_path=tmp_path)

    labels = [argv[i + 1] for i, a in enumerate(argv) if a == "--label"]
    assert f"{LABEL}={LOCK_2}" in labels


def test_the_fingerprint_a_preview_runs_with_is_read_from_its_label(monkeypatch) -> None:
    seen: list[list[str]] = []

    def _run(argv, **_):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "abc123\n", "")

    monkeypatch.setattr(vizual_sandbox.subprocess, "run", _run)

    assert vizual_sandbox._running_dependencies("vizual-nex-knowledge") == "abc123"
    assert seen == [["docker", "inspect", "-f", f'{{{{index .Config.Labels "{LABEL}"}}}}', "vizual-nex-knowledge"]]


def test_a_preview_docker_cannot_be_asked_about_has_no_fingerprint(monkeypatch) -> None:
    monkeypatch.setattr(
        vizual_sandbox.subprocess, "run", lambda argv, **_: subprocess.CompletedProcess(argv, 1, "", "No such object")
    )

    assert vizual_sandbox._running_dependencies("vizual-nex-knowledge") is None


# ── spin_up reuses only a preview on the current libraries ───────────────────────────────────────────────────────


def _sandbox(monkeypatch, tmp_path, *, started_with):
    """A running preview under its right name, started with ``started_with``; records rm / run."""
    (tmp_path / "package-lock.json").write_bytes(b'{"lock": "new"}')
    calls: list = []
    monkeypatch.setattr(vizual_sandbox, "_resolve_frontend_path", lambda slug, fp, directory="frontend": tmp_path)
    monkeypatch.setattr(vizual_sandbox, "_inspect_state", lambda name: {"exists": True, "running": True})
    monkeypatch.setattr(
        vizual_sandbox, "_running_public_host", lambda name: vizual_sandbox.public_host("nex-knowledge")
    )
    monkeypatch.setattr(vizual_sandbox, "_running_dependencies", lambda name: started_with)
    monkeypatch.setattr(vizual_sandbox, "_force_remove", lambda name: calls.append(("rm", name)))
    monkeypatch.setattr(vizual_sandbox, "_ensure_node_modules", lambda path, slug: None)
    monkeypatch.setattr(vizual_sandbox, "_write_override_config", lambda path: path)

    def _run(argv, **_):
        calls.append(("run", [a for a in argv if a.startswith(f"{LABEL}=")]))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(vizual_sandbox.subprocess, "run", _run)
    return calls


def test_a_preview_on_the_current_libraries_is_reused(monkeypatch, tmp_path) -> None:
    calls = _sandbox(monkeypatch, tmp_path, started_with=_fingerprint(b'{"lock": "new"}'))

    vizual_sandbox.spin_up("nex-knowledge")

    assert calls == []


@pytest.mark.parametrize("started_with", [_fingerprint(b'{"lock": "old"}'), ""], ids=["other libraries", "no label"])
def test_a_preview_started_with_other_libraries_is_built_anew(monkeypatch, tmp_path, started_with) -> None:
    calls = _sandbox(monkeypatch, tmp_path, started_with=started_with)

    vizual_sandbox.spin_up("nex-knowledge")

    assert calls == [
        ("rm", "vizual-nex-knowledge"),
        ("run", [f"{LABEL}={NEW}"]),
    ]


def test_two_looks_at_once_build_the_preview_once(monkeypatch, tmp_path) -> None:
    (tmp_path / "package-lock.json").write_bytes(b'{"lock": "new"}')
    current = _fingerprint(b'{"lock": "new"}')
    world = {"started_with": "old"}
    runs: list[str] = []
    monkeypatch.setattr(vizual_sandbox, "_resolve_frontend_path", lambda slug, fp, directory="frontend": tmp_path)
    monkeypatch.setattr(vizual_sandbox, "_inspect_state", lambda name: {"exists": True, "running": True})
    monkeypatch.setattr(
        vizual_sandbox, "_running_public_host", lambda name: vizual_sandbox.public_host("nex-knowledge")
    )
    monkeypatch.setattr(vizual_sandbox, "_running_dependencies", lambda name: world["started_with"])
    monkeypatch.setattr(vizual_sandbox, "_force_remove", lambda name: None)
    monkeypatch.setattr(vizual_sandbox, "_ensure_node_modules", lambda path, slug: None)
    monkeypatch.setattr(vizual_sandbox, "_write_override_config", lambda path: path)

    def _run(argv, **_):
        time.sleep(0.2)  # docker run takes a moment — the second look arrives meanwhile
        runs.append("run")
        world["started_with"] = current
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(vizual_sandbox.subprocess, "run", _run)
    looks = [threading.Thread(target=vizual_sandbox.spin_up, args=("nex-knowledge",)) for _ in range(2)]
    for look in looks:
        look.start()
    for look in looks:
        look.join()

    assert runs == ["run"]


# ── preview_is_current ───────────────────────────────────────────────────────────────────────────────────────────


def _current(monkeypatch, tmp_path, started_with):
    (tmp_path / "package-lock.json").write_bytes(b'{"lock": "new"}')
    monkeypatch.setattr(vizual_sandbox, "_resolve_frontend_path", lambda slug, fp, directory="frontend": tmp_path)
    monkeypatch.setattr(vizual_sandbox, "_running_dependencies", lambda name: started_with)
    return vizual_sandbox.preview_is_current("nex-knowledge")


def test_a_preview_on_the_current_libraries_is_current(monkeypatch, tmp_path) -> None:
    assert _current(monkeypatch, tmp_path, _fingerprint(b'{"lock": "new"}')) is True


def test_a_preview_on_other_libraries_is_not(monkeypatch, tmp_path) -> None:
    assert _current(monkeypatch, tmp_path, _fingerprint(b'{"lock": "old"}')) is False


def test_a_preview_docker_cannot_be_asked_about_is_never_rebuilt_on_a_guess(monkeypatch, tmp_path) -> None:
    assert _current(monkeypatch, tmp_path, None) is True


# ── the cockpit rebuilds it wherever it starts or looks at the preview ───────────────────────────────────────────


def _cockpit(monkeypatch, *, current: list[bool], fail: Exception | None = None):
    """``preview_state`` says running; ``preview_is_current`` answers the next of ``current``; spin_up records."""
    answers = iter(current)
    spins: list[str] = []

    def _state(slug, *, private=False):
        return {"state": "running", "url": f"https://vizual-{slug}.isnex.eu", "reason": None}

    def _spin(slug, frontend_path=None, *, private=False):
        spins.append(slug)
        if fail:
            raise fail
        return f"https://vizual-{slug}.isnex.eu"

    monkeypatch.setattr(vizual_sandbox, "preview_state", _state)
    monkeypatch.setattr(vizual_sandbox, "preview_is_current", lambda slug: next(answers))
    monkeypatch.setattr(vizual_sandbox, "spin_up", _spin)
    return spins


@pytest.mark.asyncio
async def test_looking_at_a_preview_on_old_libraries_builds_it_anew(db_session, monkeypatch) -> None:
    version, project = _make_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    spins = _cockpit(monkeypatch, current=[False])

    seen = await orchestrator.vizual_preview(db_session, version.id)

    assert spins == [project.slug]
    assert (seen["state"], seen["url"]) == ("running", f"https://vizual-{project.slug}.isnex.eu")


@pytest.mark.asyncio
async def test_looking_at_a_current_preview_leaves_it_alone(db_session, monkeypatch) -> None:
    version, _ = _make_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    spins = _cockpit(monkeypatch, current=[True])

    seen = await orchestrator.vizual_preview(db_session, version.id)

    assert spins == []
    assert seen["state"] == "running"


@pytest.mark.asyncio
async def test_a_rebuild_that_fails_reads_as_a_preview_that_does_not_run(db_session, monkeypatch) -> None:
    version, _ = _make_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    _cockpit(monkeypatch, current=[False], fail=RuntimeError("vizual sandbox: docker run failed for x: boom"))

    seen = await orchestrator.vizual_preview(db_session, version.id)

    assert seen == {
        "needed": True,
        "state": "stopped",
        "url": None,
        "reason": orchestrator.VIZUAL_PREVIEW_REBUILD_FAILED,
    }


@pytest.mark.asyncio
async def test_start_again_builds_a_preview_on_old_libraries_anew(db_session, monkeypatch) -> None:
    version, project = _make_version(db_session)
    _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    spins = _cockpit(monkeypatch, current=[False])

    after = await orchestrator.restart_vizual_preview(db_session, version.id)

    assert spins == [project.slug]
    assert after["state"] == "running"


@pytest.mark.asyncio
async def test_a_vizual_turn_ends_with_the_preview_on_the_libraries_the_agent_left(db_session, monkeypatch) -> None:
    """The round starts the preview before the agent works; the agent raises a library; the round ends with the
    preview built anew — before „Zmena je vo Vizuáli“ sends the Manažér to look at it."""
    version, project = _make_version(db_session)
    state = _seed_state(db_session, version.id, stage="vizual", actor="ai_agent")
    order: list[str] = []
    spins = _cockpit(monkeypatch, current=[False])
    calls = _stub_invoke_capture(
        monkeypatch,
        lambda s: order.append("agent")
        or _gate_report(
            s,
            vizual_checklist={"items": [{"screen": "Domov", "path": "/", "action": "Pozri.", "expected": "Odstup."}]},
        ),
    )
    real_record = orchestrator._record_message

    def _record(db, **kw):
        if kw.get("content", "").startswith("Zmena je vo Vizuáli"):
            order.append(f"zmena (spins={len(spins)})")
        return real_record(db, **kw)

    monkeypatch.setattr(orchestrator, "_record_message", _record)

    settled = await orchestrator._run_vizual_round(db_session, state, directive="povýš nex-shared")

    assert len(calls) == 1 and settled.status == "awaiting_manazer"
    # one start at the beginning of the round, one rebuild after the agent — and both before the Manažér is told
    assert spins == [project.slug, project.slug]
    assert order == ["agent", "zmena (spins=2)"]

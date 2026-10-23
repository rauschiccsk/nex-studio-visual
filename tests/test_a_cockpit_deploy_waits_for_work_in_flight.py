"""DEV-193 — the cockpit is not restarted while an agent works, Poradca answers or a deploy runs.

23.10.2026 08:30 UTC Dedo's watcher reported „no build runs“ because no ``nex-build-`` container was up — while the
Auditor was verifying NEX Inbox 1.10.1 inside the backend's own process. A deploy of the cockpit then would have cut the
verification off. The cockpit now says how much work is in flight (``/health/busy`` — counts only), and
``scripts/deploy-prod.sh`` refuses to restart the backend while anything runs, unless told why it must
(``--aj-ked-bezi "<dôvod>"``). Approved by the Director 23.10.2026 (point 3 of 14).

The script is run for real in a throwaway repository, with ``docker``, ``gh`` and ``curl`` replaced by stubs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from backend.api.routes import health
from backend.db.models.pipeline import PipelineState
from backend.services import deploy_progress
from backend.services.poradca import runner
from tests.test_orchestrator_v2_verifikacia import _make_version

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy-prod.sh"


# --- what the cockpit says -------------------------------------------------------------------------------------------


def test_the_cockpit_counts_the_work_in_flight(db_session, monkeypatch, tmp_path):
    version, _ = _make_version(db_session)
    db_session.add(
        PipelineState(
            version_id=version.id,
            flow_type="fast_fix",
            current_stage="verifikacia",
            current_actor="auditor",
            status="agent_working",
            next_action="Agent 'auditor' pracuje na fáze 'verifikacia'.",
        )
    )
    db_session.flush()
    monkeypatch.setattr(runner._slots, "_active", 1)
    deploy_progress.zacni(tmp_path / "inbox")
    try:
        assert health.busy(db_session) == {"agents_working": 1, "poradca_answering": 1, "deploys_running": 1}
    finally:
        deploy_progress.skonci(tmp_path / "inbox")
    monkeypatch.setattr(runner._slots, "_active", 0)

    assert health.busy(db_session)["deploys_running"] == 0
    assert health.busy(db_session)["poradca_answering"] == 0


def test_the_busy_route_answers_counts_only(client, db_session, monkeypatch):
    monkeypatch.setattr(health, "busy", lambda db: {"agents_working": 0, "poradca_answering": 2, "deploys_running": 0})

    response = client.get("/health/busy")

    assert response.status_code == 200
    assert response.json() == {"agents_working": 0, "poradca_answering": 2, "deploys_running": 0}


# --- what the deploy script does with it -----------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "nex-studio-visual"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / "deploy-prod.sh")
    (root / "README.md").write_text("cockpit\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t.sk")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "release")
    return root


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f'#!/usr/bin/env bash\necho "{name} $*" >> "$STUB_LOG"\n{body}\n', encoding="utf-8")
    path.chmod(0o755)


#: A stand-in for ``curl -s -o FILE -w '%{http_code}' URL``: writes ``$BUSY_BODY`` to FILE, prints ``$BUSY_CODE``.
_CURL = """out=""
while [[ $# -gt 0 ]]; do [[ "$1" == "-o" ]] && out="$2"; shift; done
[[ -n "$out" ]] && printf '%s' "$BUSY_BODY" > "$out"
printf '%s' "$BUSY_CODE"
"""


def _deploy(repo: Path, tmp_path: Path, *args: str, code: str = "200", body: dict | str = "") -> tuple:
    bin_dir = tmp_path / f"bin-{uuid.uuid4().hex[:6]}"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    _stub(bin_dir, "gh", 'echo "completed success"')
    _stub(bin_dir, "docker", "exit 0")
    _stub(bin_dir, "curl", _CURL)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "DOCKER_HOST": "unix:///nonexistent",
        "BUSY_CODE": code,
        "BUSY_BODY": body if isinstance(body, str) else json.dumps(body),
    }
    done = subprocess.run(
        ["bash", "scripts/deploy-prod.sh", "--dry-run", "--version", "9.9.9", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return done, log.read_text(encoding="utf-8").splitlines()


IDLE = {"agents_working": 0, "poradca_answering": 0, "deploys_running": 0}
#: The refusal that follows the question in a throwaway checkout — reaching it means the question let the deploy on.
#: Which one depends on the machine: the live compose is there on ANDROS, not in CI.
NEXT_REFUSAL = re.compile(
    r"ODMIETNUTÉ: (compose súbor \S+ neexistuje|z projektu nex-studio-visual-prod nebeží ani jeden kontajner)"
)


def test_work_in_flight_refuses_the_restart_and_says_what_runs(repo, tmp_path):
    done, calls = _deploy(repo, tmp_path, "all", body={**IDLE, "agents_working": 1, "poradca_answering": 1})

    assert done.returncode == 1
    assert (
        "ODMIETNUTÉ: v kokpite práve beží: agent pracuje na 1 stavbe, Poradca odpovedá na 1 otázku. Reštart backendu "
        'by to prerušil — počkaj, kým dobehne, alebo nasaď len frontend. Ak to musí byť teraz: --aj-ked-bezi "dôvod".'
    ) in done.stderr
    assert not [c for c in calls if c.startswith("docker")]


def test_nothing_in_flight_lets_the_deploy_go_on(repo, tmp_path):
    done, _calls = _deploy(repo, tmp_path, "all", body=IDLE)

    assert NEXT_REFUSAL.search(done.stderr), done.stderr
    assert "v kokpite práve beží" not in done.stderr


def test_a_reason_lets_it_go_on_and_is_said(repo, tmp_path):
    busy = {**IDLE, "deploys_running": 1}

    done, _calls = _deploy(repo, tmp_path, "--aj-ked-bezi", "Director: oprava hneď", "all", body=busy)

    assert NEXT_REFUSAL.search(done.stderr), done.stderr
    assert "nasadzujem napriek tomu, že beží: 1 nasadenie — dôvod: Director: oprava hneď" in done.stderr


def test_the_screens_alone_do_not_restart_the_backend_and_ask_nothing(repo, tmp_path):
    done, calls = _deploy(repo, tmp_path, "frontend", body={**IDLE, "agents_working": 3})

    assert NEXT_REFUSAL.search(done.stderr), done.stderr
    assert not [c for c in calls if c.startswith("curl")]


def test_a_cockpit_that_cannot_tell_is_not_taken_for_idle(repo, tmp_path):
    done, _calls = _deploy(repo, tmp_path, "all", code="500", body="Internal Server Error")

    assert done.returncode == 1
    assert "ODMIETNUTÉ: kokpit nevie povedať, či niečo beží (HTTP 500)" in done.stderr


def test_a_cockpit_from_before_the_question_is_said_and_let_go_on(repo, tmp_path):
    done, _calls = _deploy(repo, tmp_path, "all", code="404", body="Not Found")

    assert NEXT_REFUSAL.search(done.stderr), done.stderr
    assert "kokpit ešte nevie povedať, či niečo beží (stará verzia) — over ručne" in done.stderr


def test_it_asks_again_after_the_build_before_anything_live_changes():
    """The build takes minutes; work started meanwhile is asked about once more — before the live compose is rewritten,
    so a refusal leaves it naming the images that really run. Read from the script: running it that far would
    rewrite the live compose."""
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    calls = [i for i, line in enumerate(lines) if line.strip() == "busy_check"]
    last_build = max(i for i, line in enumerate(lines) if line.strip().startswith("docker build"))
    first_live_change = min(i for i, line in enumerate(lines) if 'cp -a "$COMPOSE"' in line or "compose -f" in line)

    assert any(last_build < i < first_live_change for i in calls)

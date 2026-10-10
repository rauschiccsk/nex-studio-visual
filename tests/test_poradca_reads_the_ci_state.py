"""DEV-53 — Poradca's ``ci`` tool reads the cockpit's real answer about CI.

The tool read ``snap.state`` while the answer (``ci_status.StavZostavenia``) carries ``stav``: every call ended in
``AttributeError`` and Poradca could say nothing about a build — not even "why is CI red", the question the tool
exists for. No test ever ran the tool, so nothing noticed (found 10.10.2026 while working on DEV-51).

Director 10.10.2026: „Založ tiket“ → „Áno, začni DEV-53“.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import textwrap
import uuid

import pytest

from backend.services import ci_status
from backend.services.poradca import tools

RUN = "https://github.com/rauschiccsk/nex-inbox/actions/runs/38042619283"


def _answer(stav, detail, *, url=None, bezi=False):
    return ci_status.StavZostavenia(stav, detail, "48de08076e59", 0.0, bezi=bezi, url=url)


@pytest.fixture
def poradca(monkeypatch, tmp_path):
    """The tool of one question, on a project directory that is a GitHub checkout."""
    monkeypatch.setattr(tools.PoradcaTools, "_project_dir", lambda self, db: str(tmp_path))
    commands: list[list[str]] = []

    async def _run(argv, *, cwd=None, timeout=0):
        commands.append(argv)
        if "remote.origin.url" in argv:
            return 0, "https://github.com/rauschiccsk/nex-inbox.git\n"
        if argv[:3] == ["gh", "run", "view"]:
            return 0, "\n".join(f"riadok {i}" for i in range(500)) + "\nASSERTION FAILED: front vrátil 504"
        raise AssertionError(f"neočakávaný príkaz {argv}")

    monkeypatch.setattr(tools, "_run", _run)
    tool = tools.PoradcaTools(project_id=uuid.uuid4(), version_id=None, user_id=uuid.uuid4())
    return tool, commands


def _snapshot(monkeypatch, answer, rows=None):
    async def _snap(_root):
        return answer

    async def _behy(_root, _repo, _sha):
        return rows or [], None

    monkeypatch.setattr(ci_status, "snapshot", _snap)
    monkeypatch.setattr(ci_status, "_behy_z_githubu", _behy)


async def test_a_red_build_comes_with_its_run_and_the_end_of_the_failed_log(poradca, monkeypatch):
    tool, commands = poradca
    _snapshot(
        monkeypatch,
        _answer("red", "CI zlyhalo (postup Release smoke gate, beh 38042619283, failure)", url=RUN),
        rows=[
            {
                "status": "completed",
                "conclusion": "failure",
                "databaseId": 38042619283,
                "workflowName": "Release smoke gate",
            }
        ],
    )

    out = await tool.ci({})

    assert "CI zlyhalo (postup Release smoke gate, beh 38042619283, failure)" in out
    assert RUN in out, "Poradca nevie povedať, kde sa beh dá pozrieť"
    assert "ASSERTION FAILED: front vrátil 504" in out, "chýba koniec logu zlyhaného kroku"
    assert ["gh", "run", "view", "38042619283", "-R", "rauschiccsk/nex-inbox", "--log-failed"] in commands


async def test_a_green_build_is_said_plainly_and_no_log_is_fetched(poradca, monkeypatch):
    tool, commands = poradca
    _snapshot(monkeypatch, _answer("green", "CI zelené (postup CI, beh 5)"))

    out = await tool.ci({})

    assert "green" in out and "CI zelené (postup CI, beh 5)" in out
    assert commands == [], "pri zelenom CI netreba GitHub ani git"


async def test_not_knowing_is_said_as_not_knowing(poradca, monkeypatch):
    tool, _commands = poradca
    _snapshot(monkeypatch, _answer("unknown", "CI ešte beží (postup CI, beh 7)", url=RUN, bezi=True))

    out = await tool.ci({})

    assert "unknown" in out and "CI ešte beží" in out
    assert RUN in out


def test_the_tool_reads_only_what_the_answer_carries():
    """The class of the bug, not the instance: every attribute the tool reads from the CI answer must be a field
    of that answer. A renamed or misremembered field then fails here, not in front of the Manažér."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(tools.PoradcaTools.ci)))
    snapshot_names = {
        node.targets[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and "snapshot" in ast.unparse(node.value)
    }
    read = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in snapshot_names
    }
    fields = {f.name for f in dataclasses.fields(ci_status.StavZostavenia)}

    assert snapshot_names, "stráž nenašla odpoveď o CI v nástroji — nič by nestrážila"
    assert read, "stráž nenašla jediné čítanie odpovede — nič by nestrážila"
    assert read <= fields, f"nástroj číta polia, ktoré odpoveď nemá: {sorted(read - fields)}"

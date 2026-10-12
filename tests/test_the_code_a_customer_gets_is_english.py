"""DEV-65 — the source code a customer receives is all English; only what the application shows is in Slovak.

Director 11.10.2026: „Všetko čo bude zobrazené v kokpite zostáva po slovensky. Ak napríklad spravíme aplikáciu
zákazníkovi pre zákazku, tak keď zákazníkovi odovzdávame zdrojové kódy, tam má byť všetko po anglicky.“ The agents'
charters said nothing about the language of code. Now every coding brief says it, and Verifikácia measures it: Slovak
text in the comments, docstrings, test messages and test names a version added floors the verdict to FAIL.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import textwrap
from pathlib import Path

import pytest

from backend.services import orchestrator, source_language, uat_guide
from tests.test_orchestrator_v2_verifikacia import (
    _ban_deploy_calls,
    _make_version,
    _msgs,
    _seed_verifikacia,
    _stub_auditor,
    _stub_smoke,
    _verdict_pass,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
        env={"GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@t", "GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@t",
             "PATH": "/usr/bin:/bin"},
    ).stdout.strip()  # fmt: skip


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for path, text in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> Path:
    path = tmp_path / "app"
    path.mkdir()
    _git(path, "init", "-q")
    return path


BASE = {
    "backend/app/old.py": "# Starý komentár, ktorý táto verzia nemenila\nx = 1\n",
}

VERSION = {
    # flagged
    "backend/app/money.py": (
        '"""Výpočet DPH pre faktúru."""\n'
        "\n"
        "def total(lines):\n"
        "    # zaokrúhli až na konci\n"
        "    return sum(lines)  # sum of the lines\n"
    ),
    "backend/tests/test_money.py": (
        "def test_total():\n"
        '    assert total([1, 2]) == 3, "súčet nesedí"\n'
        '    assert label() == "Faktúra uložená", "the screen says it was saved"\n'
    ),
    "frontend/src/Invoice.tsx": (
        "// Obrazovka faktúry\n"
        "export function Invoice() {\n"
        '  const url = "https://example.com/a"; // the API, not a comment start in the string\n'
        '  return <p title="Uložené">Faktúra je uložená</p>;\n'
        "}\n"
    ),
    "frontend/src/__tests__/Invoice.test.tsx": (
        'it("ukáže uloženú faktúru", () => {\n'
        '  expect(screen.getByText("Faktúra je uložená")).toBeTruthy();\n'
        "});\n"
        'it("shows the saved invoice", () => {});\n'
    ),
    "deploy/run.sh": "#!/bin/sh\n# spustí aplikáciu\necho 'Štart'\n",
    # not flagged
    "backend/app/screen.py": (
        "# The screen says „Faktúra uložená“ after saving — quoted UI text stays as the app shows it.\n"
        'SAVED = "Faktúra uložená"\n'
    ),
    "docs/specs/návod.md": "# Návod\nToto je dokumentácia po slovensky.\n",
    "MEMORY.md": "Poznámky agenta.\n",
    "frontend/src/api.generated.ts": "// auto-generated — nemeniť\nexport type A = 1;\n",
}


def test_the_check_finds_slovak_only_where_a_customer_would_read_code(repo):
    base = _commit(repo, BASE, "base")
    head = _commit(repo, VERSION, "version")

    found = source_language.scan(repo, base, head, "1.0.0")

    assert [(f.location(), f.what) for f in found] == [
        ("backend/app/money.py:1", "docstring"),
        ("backend/app/money.py:4", "komentár"),
        ("backend/tests/test_money.py:2", "hláška skúšky"),
        ("deploy/run.sh:2", "komentár"),
        ("frontend/src/Invoice.tsx:1", "komentár"),
        ("frontend/src/__tests__/Invoice.test.tsx:1", "meno skúšky"),
    ]


def test_a_comment_the_version_did_not_touch_is_not_its_problem(repo):
    base = _commit(repo, BASE, "base")
    head = _commit(repo, {"backend/app/old.py": "# Starý komentár, ktorý táto verzia nemenila\nx = 2\n"}, "v")

    assert source_language.scan(repo, base, head, "1.0.0") == []


def test_a_version_that_translates_the_comment_it_touches_is_clean(repo):
    base = _commit(repo, BASE, "base")
    head = _commit(repo, {"backend/app/old.py": "# An old comment, now in English\nx = 2\n"}, "v")

    assert source_language.scan(repo, base, head, "1.0.0") == []


# DEV-80, 12.10.2026: a quotation opened with „ and closed with a straight " — the way NEX Inbox writes 115 of its
# lines — was read as Slovak in code, so the verification stage would have reported what is not there. A quotation is
# one quotation whichever mark closes it; straight quotes inside one stay in it, and Slovak between two quotations is
# still Slovak.
@pytest.mark.parametrize(
    ("line", "slovak"),
    [
        ('# The Director: „Áno, súhlasím" — keep it.', False),
        ("# The Director: „Áno, súhlasím“ — keep it.", False),
        ('# The screen says „Klikni na "Uložiť" a pokračuj“ after saving.', False),
        ('# Press „Uložiť" and then „Zrušiť“ to leave.', False),
        ('# Press „Uložiť" potom vráť „Zrušiť“.', True),
        ("# Skontroluj vstup a vráť chybu.", True),
        ("# The Director wrote „Áno, súhlasím and nothing closed it.", True),
        ('# Press „Uložiť a vráť „Zrušiť" now.', True),
    ],
)
def test_a_quotation_is_one_quotation_whichever_mark_closes_it(line, slovak):
    assert source_language.is_slovak(line) is slovak


# --- Verifikácia ---------------------------------------------------------------------------------------------------


def _guide(version) -> dict[str, str]:
    """The UAT test guide the agent writes while it builds (DEV-90) — without it the version does not pass."""
    return {
        uat_guide.relative_path(version.version_number): (
            "## Uloženie faktúry\nObrazovka: Faktúry\n1. Ulož faktúru.\nOčakávané: Faktúra je v zozname.\n"
        )
    }


def _project_repo(monkeypatch, tmp_path, project, files) -> None:
    root = tmp_path / "projects"
    path = root / project.slug
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    base = _commit(path, BASE, "base")
    _commit(path, files, "version")
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", root)
    monkeypatch.setattr(orchestrator.delivery_statement, "base_plan", lambda db, version: [("sha", base)])


async def test_slovak_code_floors_the_auditors_pass_and_reaches_the_fixer(db_session, monkeypatch, tmp_path):
    version, project = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)
    _project_repo(
        monkeypatch, tmp_path, project, {"backend/app/money.py": "# zaokrúhli až na konci\nx = 1\n", **_guide(version)}
    )
    _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch)
    _ban_deploy_calls(monkeypatch)

    async def _no_critic(*_a, **_k):
        raise AssertionError("a FAIL only the cockpit measured has no Auditor fix scope to vet")

    monkeypatch.setattr(orchestrator, "_run_fix_critique", _no_critic)

    await orchestrator.run_dispatch(db_session, version.id)

    verdict = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"][-1]
    finding = {
        "text": (
            "Kód, ktorý dostane zákazník, má slovenský text na 1 mieste (komentáre, docstringy, hlášky alebo mená "
            "skúšok) — prelož ho do angličtiny; text, ktorý aplikácia zobrazí používateľovi, nemeň: "
            "backend/app/money.py:1."
        ),
        "blocking": True,
        "escapes_tests": False,
    }
    assert verdict.payload["verdict"] == "FAIL"
    assert verdict.payload["engine_override"] == "source_language"
    assert verdict.payload["findings"][-1] == finding
    assert verdict.content.startswith("Verzia funguje, ale kód, ktorý dostane zákazník, má slovenský text")
    assert orchestrator._verifikacia_passed(db_session, version.id) is False
    assert f"- {finding['text']}" in orchestrator._latest_verifikacia_fix_scope(db_session, version.id)


async def test_slovak_screen_text_does_not_stop_a_release(db_session, monkeypatch, tmp_path):
    version, project = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)
    _project_repo(
        monkeypatch,
        tmp_path,
        project,
        {"backend/app/screen.py": 'SAVED = "Faktúra uložená"  # what the screen says\n', **_guide(version)},
    )
    monkeypatch.setattr(orchestrator, "_commit_release_note", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_git_tag_version", lambda *a, **k: None)

    monkeypatch.setattr(orchestrator, "_push_release_artifacts", lambda *a, **k: None)
    _stub_auditor(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch)
    _ban_deploy_calls(monkeypatch)

    await orchestrator.run_dispatch(db_session, version.id)

    assert orchestrator._verifikacia_passed(db_session, version.id) is True


async def test_a_range_that_cannot_be_read_never_stops_a_release_on_a_guess(db_session, monkeypatch):
    version, _project = _make_version(db_session)

    async def _boom(*_a, **_k):
        raise source_language.GitUnreadable("broken repo")

    monkeypatch.setattr(orchestrator, "run_blocking", _boom)

    assert await orchestrator._source_language_finding(db_session, version.id, "x", "1.0.0") is None


def test_the_check_runs_off_the_main_loop():
    """ICCINT-74: git waits, so the check runs in a thread. The cockpit's main-loop guard does not look at calls
    inside the orchestrator yet (ICCINT-82), so this one does it for the check."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(orchestrator._source_language_finding)))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    direct = [c for c in calls if getattr(c.func, "id", None) == "_scan_version_source"]
    offloaded = [
        c
        for c in calls
        if getattr(c.func, "id", None) == "run_blocking"
        and c.args
        and getattr(c.args[0], "id", None) == "_scan_version_source"
    ]
    assert direct == [] and len(offloaded) == 1


# --- the agent is told ---------------------------------------------------------------------------------------------


def test_every_coding_brief_carries_the_rule(db_session):
    from backend.db.models.tasks import Task

    task = Task(number=1, title="Faktúra", task_type="backend", status="todo")
    for flow in ("new_version", "fast_fix"):
        assert orchestrator.SOURCE_IN_ENGLISH_RULE in orchestrator._directive_for_build_task(task, None, [], flow)


def test_the_vizual_briefs_carry_the_rule(db_session, monkeypatch):
    version, _project = _make_version(db_session)
    monkeypatch.setattr(orchestrator, "_vizual_preview_rule", lambda _root: "")
    first = orchestrator._vizual_directive(db_session, version.id, None)
    change = orchestrator._vizual_directive(db_session, version.id, "Väčšie tlačidlo")
    mockup = orchestrator._vizual_directive(db_session, version.id, "Väčšie tlačidlo", mockup_rel="mockup.html")

    assert all(orchestrator.SOURCE_IN_ENGLISH_RULE in brief for brief in (first, change, mockup))


def test_the_charters_say_it():
    root = Path(orchestrator.__file__).resolve().parents[2] / "templates"
    assert "**Zdrojový kód je celý po anglicky (DEV-65).**" in (root / "ai-agent-charter.md").read_text("utf-8")
    assert "**Zdrojový kód po anglicky (DEV-65)**" in (root / "auditor-charter.md").read_text("utf-8")

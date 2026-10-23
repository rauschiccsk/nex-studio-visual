"""DEV-183 — Slovak in the code a customer receives is caught without its diacritics too, and in the task that wrote it.

22.10.2026 Career Asistent 1.1.0: the source-language check (DEV-65) ran only at Verifikácia and knew Slovak only by
its letters. It found four places; the Auditor found six more — „most of them without diacritics, so the automatic
check does not see them“ (test group names „Ponuky“, „Dokumenty“, ``test_override_aplikacie_422``) — and its review of
the fix eleven more: two more rounds of fixes and two full verifications. Director 22.10.2026: „Súhlasím“ — the check
runs after every finished task of Programovanie and knows Slovak written without diacritics by its words.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import sysconfig
import tokenize
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import build_leftovers, claude_agent, orchestrator, source_language
from backend.services.pipeline_status import PipelineStatusBlock

# ── the rule ─────────────────────────────────────────────────────────────────────────────────────────────────────

#: The places of Career Asistent 1.1.0 the Auditor and its review found (22.10.2026), as the code had them.
CAREER_ASISTENT = [
    ("web/src/test/offers.test.tsx", 'describe("Ponuky", () => {\n  it("lists the offers", () => {});\n});\n', True),
    ("web/src/test/application.test.tsx", 'describe("Reakcia a pohovory", () => {});\n', True),
    ("web/src/test/documents.test.tsx", 'describe("CV a list — kontrola pravdivosti a export", () => {});\n', True),
    ("backend/tests/test_evaluate_offer.py", "def test_override_bez_opory_422():\n    pass\n", True),
    ("backend/tests/test_evaluate_offer.py", "async def test_override_aplikacie_422():\n    pass\n", True),
    ("backend/tests/test_unassigned.py", "class TestNezaradenaPosta:\n    pass\n", True),
    ("web/src/preview/fixtures.ts", "// ---- Dokumenty -------------------------------\nexport const a = 1;\n", False),
    ("backend/app/api/reminders.py", '"""Pripomienky (S§9.5, design.md §8) — the active reminders."""\n', False),
    ("frontend/src/components/layout/Header.tsx", "/* right slot (top-right vzor). */\n", False),
]


@pytest.mark.parametrize(("path", "source", "is_test"), CAREER_ASISTENT)
def test_slovak_written_without_diacritics_is_found(path, source, is_test):
    found = source_language.slovak_in(path, source, is_test=is_test)

    assert [f.line for f in found] == [1], f"{path}: {source!r}"


def test_a_python_test_is_read_by_its_name():
    found = source_language.slovak_in(
        "backend/tests/test_offers.py",
        "def test_a_retired_address_is_never_given_again():\n    pass\n\n\ndef test_zrusena_adresa():\n    pass\n",
        is_test=True,
    )

    assert [(f.line, f.what, f.text) for f in found] == [(5, source_language.WHAT_TEST_NAME, "test_zrusena_adresa")]


ENGLISH = [
    '# the „Ponuky“ tab and the "Dokumenty" heading keep the words the screen shows\n',
    "# the page /aktualizacie and the folder web/src/pages/profil are the application's own names\n",
    "# lists the offers, the documents and the record of applications\n",
]


@pytest.mark.parametrize("line", ENGLISH)
def test_english_and_what_the_application_shows_are_left_alone(line):
    assert source_language.slovak_in("backend/app/offers.py", line) == []


def test_the_projects_own_name_is_not_a_slovak_sentence():
    line = "# Career Asistent sends the digest\n"
    own = frozenset({"career", "asistent"})

    assert source_language.slovak_in("backend/app/digest.py", line, own=own) == []
    assert [f.line for f in source_language.slovak_in("backend/app/digest.py", line)] == [1], "the name is a word"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def _repo(work: Path) -> str:
    work.mkdir(parents=True)
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "studio@isnex.eu")
    _git(work, "config", "user.name", "NEX Studio")
    (work / ".gitignore").write_text("".join(f"{line}\n" for line in build_leftovers.IGNORED), encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "chore: start")
    return _git(work, "rev-parse", "HEAD")


def _commit(work: Path, path: str, content: str) -> str:
    (work / path).parent.mkdir(parents=True, exist_ok=True)
    (work / path).write_text(content, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", f"feat: {path}")
    return _git(work, "rev-parse", "HEAD")


def test_the_scan_knows_the_project_by_its_repository(tmp_path):
    work = tmp_path / "career-asistent"
    base = _repo(work)
    head = _commit(work, "ops/backup.sh", "#!/bin/sh\n# Career Asistent backup\n# zaloha databazy\n")

    found = source_language.scan(work, base, head)

    assert [(f.path, f.line) for f in found] == [("ops/backup.sh", 3)]


OFFERS = 'describe("the offer list", () => {});\n\ndescribe("Ponuky", () => {});\n'


def test_a_file_the_version_moved_is_its_new_code(tmp_path):
    """Career Asistent 1.1.0 moved the tests of 1.0.0 from frontend/ to web/; git counts a move as no added line, so the
    check saw none of „Ponuky“, „Dokumenty“ … and the Auditor opened a second round for them. Director 22.10.2026:
    „Súhlasím“ — a file the version moves or copies is delivered at its new place, as new code of the version."""
    work = tmp_path / "career-asistent"
    _repo(work)
    _commit(work, "frontend/src/test/offers.test.tsx", OFFERS)
    _commit(work, "frontend/src/test/untouched.test.tsx", 'describe("Dokumenty", () => {});\n')
    base = _git(work, "rev-parse", "HEAD")
    (work / "web/src/test").mkdir(parents=True)
    _git(work, "mv", "frontend/src/test/offers.test.tsx", "web/src/test/offers.test.tsx")
    head = _commit(work, "web/src/test/copied.test.tsx", OFFERS)

    found = source_language.scan(work, base, head)

    assert [(f.path, f.line) for f in found] == [
        ("web/src/test/copied.test.tsx", 3),
        ("web/src/test/offers.test.tsx", 3),
    ]


# ── the list of words ────────────────────────────────────────────────────────────────────────────────────────────


def test_the_list_holds_plain_lowercase_words_once_each():
    lines = [line for line in source_language.WORDS_FILE.read_text(encoding="utf-8").splitlines()]
    words = [line for line in lines if line and not line.startswith("#")]

    assert len(words) > 10000, "the list was read wrong — the rule would know no Slovak word"
    assert [w for w in words if not re.fullmatch(r"[a-z]{4,}", w)] == []
    assert words == sorted(set(words))
    assert source_language.SLOVAK_WORDS == frozenset(words)


def _english_code() -> list[Path]:
    """Python's own library — English code with comments and docstrings, the same on every machine the cockpit runs.
    Its test suite and the packages Ubuntu ships apart (tkinter, idlelib, turtledemo, lib2to3) are not on each of them,
    so they are left out: the guard reads the same code wherever it runs."""
    root = Path(sysconfig.get_paths()["stdlib"])
    skip = {"test", "tests", "idle_test", "idlelib", "tkinter", "turtledemo", "lib2to3"}
    skip |= {"site-packages", "dist-packages", "__pycache__"}
    return sorted(p for p in root.rglob("*.py") if not skip & set(p.relative_to(root).parts))


def _parts(source: str):
    """Every comment, docstring and test name of one module — what the rule reads, read here on its own."""
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                yield tok.string
        tree = ast.parse(source)
    except (tokenize.TokenError, SyntaxError, IndentationError, ValueError):
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node.name
        doc = (
            ast.get_docstring(node, clean=False)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            else None
        )
        if doc:
            yield doc


def test_no_word_of_the_list_is_a_word_of_english_code():
    files = _english_code()
    hits: dict[str, str] = {}
    read = 0
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        read += 1
        for text in _parts(source):
            for word in source_language.slovak_words(text):
                hits.setdefault(word, f"{path.name}: {text.strip()[:80]}")

    assert read > 300, "Python's library was not found — the guard would guard nothing"
    assert hits == {}


# ── after every task ─────────────────────────────────────────────────────────────────────────────────────────────


def _build(db, projects_root: Path):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="x", role="ri"
    )
    db.add(user)
    db.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
        miera_autonomie="po_kazdej_faze",
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="1.1.0")
    db.add(version)
    db.flush()
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type="new_version",
            current_stage="programovanie",
            current_actor="ai_agent",
            status="agent_working",
            next_action="working",
        )
    )
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="Offers", status="planned")
    db.add(epic)
    db.flush()
    feat = Feat(epic_id=epic.id, number=1, title="Evaluation", status="todo")
    db.add(feat)
    db.flush()
    db.add(Task(feat_id=feat.id, number=1, title="Override", task_type="backend", status="todo"))
    db.flush()
    work = projects_root / project.slug
    _repo(work)
    return version, work


SLOVAK_TEST = "def test_override_aplikacie_422():\n    pass\n"
ENGLISH_TEST = "def test_an_override_without_an_application_is_422():\n    pass\n"


def _agent(monkeypatch, work: Path, contents: list[str]) -> list[str]:
    """Turn by turn the agent writes the next content into its test file and commits it; the prompts are kept."""
    prompts: list[str] = []

    async def _turn(db, *, version_id, role, stage, prompt, **_kw):
        prompts.append(prompt)
        sha = _commit(work, "backend/tests/test_offers.py", contents[min(len(prompts), len(contents)) - 1])
        return PipelineStatusBlock(
            stage="programovanie", kind="gate_report", summary="hotovo", awaiting="manazer", commits=[sha]
        )

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _turn)
    monkeypatch.setattr(orchestrator, "verify_mechanical", lambda slug, block, baseline_sha=None: None)
    return prompts


def _task(db, version) -> Task:
    return db.execute(
        select(Task)
        .join(Feat, Feat.id == Task.feat_id)
        .join(Epic, Epic.id == Feat.epic_id)
        .where(Epic.version_id == version.id)
    ).scalar_one()


def _returns(db, version) -> list[str]:
    messages = db.execute(
        select(PipelineMessage).where(PipelineMessage.version_id == version.id).order_by(PipelineMessage.seq)
    ).scalars()
    return [m.content for m in messages if m.kind == "return" and m.recipient == orchestrator.AI_AGENT_ROLE]


REASON = (
    "the code this task added has Slovak text in 1 place(s) — the code a customer receives is all English: translate "
    "these comments, docstrings, test messages and test names (Slovak written without diacritics too) and leave every "
    "text the application shows unchanged: backend/tests/test_offers.py:1: test_override_aplikacie_422"
)


async def test_slovak_a_task_added_goes_back_to_the_agent_in_the_same_task(db_session, monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects)
    version, work = _build(db_session, projects)
    prompts = _agent(monkeypatch, work, [SLOVAK_TEST, ENGLISH_TEST])

    await orchestrator.run_dispatch(db_session, version.id)

    assert len(prompts) == 2
    assert _returns(db_session, version) == [f"Self-check 1/{orchestrator._SELF_CHECK_RETRIES} (úloha #1): {REASON}"]
    assert REASON in prompts[1], "the agent's second try was not told what to translate"
    assert _task(db_session, version).status == "done"


async def test_a_task_is_never_failed_for_its_language(db_session, monkeypatch, tmp_path):
    """The last try is not checked — what is left the Verifikácia check finds, as before DEV-183."""
    projects = tmp_path / "projects"
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects)
    monkeypatch.setattr(orchestrator, "_SELF_CHECK_RETRIES", 3)
    version, work = _build(db_session, projects)
    prompts = _agent(monkeypatch, work, [SLOVAK_TEST, SLOVAK_TEST + "\n\n# zaloha\n", SLOVAK_TEST + "\n"])

    await orchestrator.run_dispatch(db_session, version.id)

    assert len(prompts) == 3
    assert len(_returns(db_session, version)) == 2
    assert _task(db_session, version).status == "done"


async def test_english_code_goes_through_at_once(db_session, monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects)
    version, work = _build(db_session, projects)
    prompts = _agent(monkeypatch, work, [ENGLISH_TEST])

    await orchestrator.run_dispatch(db_session, version.id)

    assert len(prompts) == 1
    assert _returns(db_session, version) == []
    assert _task(db_session, version).status == "done"

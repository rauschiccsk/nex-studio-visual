"""DEV-340 — the person working with the cockpit is the Manažér, never „Director“.

10.11.2026: the charters of the cockpit's agents said „Director“ in ten places, the agents carried the word into their
answers (Poradca of Nápady in 3 answers, Poradca of the projects in 11, the Staviteľ in a build's messages) and people
took it for their company's director. The rule reads everything an agent is given and everything a person reads:
every file of templates/ (charters, skills, what a project is founded with), every text the backend says (its strings,
not its docstrings — those are the code's own notes) and the cockpit's release notes on the Aktualizácie tab. The
screens' texts are guarded beside them (frontend test_the_manazer_is_never_called_director.test.ts).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from backend.services import release_notes

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
BACKEND = ROOT / "backend"
#: „Director“ in any form (Directora, Directorovi, Director-approved) — never the English „Directory“.
OLD_NAME = re.compile(r"\bDirector(?!y|ies)")
#: Third-party code vendored under templates/ (the dead-code tool) and compiled files are no text of ours.
_NOT_OURS = {"node_modules", "__pycache__"}


def template_files() -> list[Path]:
    return [
        path
        for path in sorted(TEMPLATES.rglob("*"))
        if path.is_file() and not _NOT_OURS & set(path.relative_to(TEMPLATES).parts) and path.suffix != ".pyc"
    ]


def strings_of(source: str) -> list[tuple[int, str]]:
    """Every string a Python source says, its docstrings left out."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def backend_sources() -> list[Path]:
    return [path for path in sorted(BACKEND.rglob("*.py")) if "tests" not in path.relative_to(BACKEND).parts]


def test_no_charter_skill_or_template_an_agent_is_given_says_director():
    said = [
        f"{path.relative_to(ROOT)}:{n}: {line.strip()[:100]}"
        for path in template_files()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if OLD_NAME.search(line)
    ]
    assert said == []


def test_no_text_the_backend_says_calls_anybody_director():
    said = [
        f"{path.relative_to(ROOT)}:{n}: {' '.join(text.split())[:100]}"
        for path in backend_sources()
        for n, text in strings_of(path.read_text(encoding="utf-8"))
        if OLD_NAME.search(text)
    ]
    assert said == []


def test_no_release_note_on_the_aktualizacie_tab_says_director():
    notes = sorted(release_notes.DOCS_VERSIONS_ROOT.glob(release_notes.RELEASE_NOTES_GLOB))
    said = [str(path.relative_to(ROOT)) for path in notes if OLD_NAME.search(path.read_text(encoding="utf-8"))]
    assert said == []


def test_the_rule_reads_real_files_and_sees_every_form_of_the_word():
    names = {path.relative_to(TEMPLATES).as_posix() for path in template_files()}
    assert {"poradca-charter.md", "ai-agent-charter.md", "skills/multi-customer/SKILL.md"} <= names
    assert len(backend_sources()) > 200
    assert len(list(release_notes.DOCS_VERSIONS_ROOT.glob(release_notes.RELEASE_NOTES_GLOB))) > 100
    for said in ("rozhodne Director", "rozhodnutie Directora", "Directorovi", "Director-approved", "Director's"):
        assert OLD_NAME.search(said), said
    for said in ("Directory", "Directories", "Manažér"):
        assert not OLD_NAME.search(said), said


def test_the_backend_rule_reads_every_string_but_the_docstrings():
    source = (
        '"""The module, as the Director wanted."""\n'
        "def f(who):\n"
        '    """The Director decides."""\n'
        '    return f"O verzii rozhoduje Director, {who}" + "a ďalej"\n'
    )
    said = [text for _n, text in strings_of(source) if OLD_NAME.search(text)]
    assert said == ["O verzii rozhoduje Director, "]

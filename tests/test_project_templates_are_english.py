"""DEV-182 — what the cockpit puts into a project as the project's code is English before any project gets it.

The code a customer receives is all English (DEV-65); the cockpit checks what a version adds at its Verifikácia. A
template the cockpit copies into a project when it creates it or switches it to Web is older than any version, so that
check never reads it. 22.10.2026 the Auditor of Career Asistent 1.1.0 found „Štandardný“ in its
``.github/workflows/web.yml`` — a comment of ``templates/github-actions-web-workflow.yml`` — and the version got one
more round of fixes for it. Every next Web project would have got the same. Director 22.10.2026: „Súhlasím“.

The templates are found in the code that copies them, not listed here; they are checked by the same rule that checks a
version's code (:func:`source_language.slovak_in`). The agents' instructions (Markdown charters, ``CLAUDE.md``,
skills) are the cockpit's own files in the project and stay Slovak.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.services import create_project_postscaffold, source_language

SCAFFOLD = Path(create_project_postscaffold.__file__)
TEMPLATES = create_project_postscaffold.NEX_STUDIO_TEMPLATES


def _copied_templates() -> list[str]:
    """Every file the scaffold takes from the templates by a module-level name (``X = NEX_STUDIO_TEMPLATES / "…"``),
    without the agents' Markdown instructions."""
    names = []
    for node in ast.parse(SCAFFOLD.read_text(encoding="utf-8")).body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.BinOp)):
            continue
        left, right = node.value.left, node.value.right
        if (
            isinstance(left, ast.Name)
            and left.id == "NEX_STUDIO_TEMPLATES"
            and isinstance(right, ast.Constant)
            and isinstance(right.value, str)
            and not right.value.endswith(".md")
            and (TEMPLATES / right.value).is_file()
        ):
            names.append(right.value)
    return sorted(names)


COPIED = _copied_templates()


def test_the_scaffold_names_the_templates_a_project_gets():
    """A rule that finds nothing would guard nothing — the web CI of 22.10.2026 must be among them."""
    assert "github-actions-web-workflow.yml" in COPIED
    assert "pre-commit-hook.sh" in COPIED


@pytest.mark.parametrize("name", COPIED)
def test_a_template_that_becomes_a_projects_code_is_english(name):
    found = source_language.slovak_in(name, (TEMPLATES / name).read_text(encoding="utf-8"))

    assert [f"{f.location()} ({f.what}): {f.text}" for f in found] == []


def test_the_rule_reads_every_template_it_is_given():
    """Each copied template is of a kind the rule reads — one it skipped would pass unread."""
    assert [n for n in COPIED if source_language._parts_of(n, "", False) is None] == []


def test_a_comment_with_an_apostrophe_is_read_whole():
    """The line that let „Štandardný“ through: two apostrophes of English took the word between them for a string."""
    line = "# on the project's own runner. It is its own file so a Štandardný project's ci.yml stays"

    assert [f.line for f in source_language.slovak_in("web.yml", line)] == [1]


def test_a_hash_inside_quotes_does_not_start_a_comment():
    assert source_language.slovak_in("ci.yml", 'run: echo "pošta #1 je v poriadku"\n') == []
    assert source_language.slovak_in("ci.sh", "echo 'žiadny # komentár' # but this one is English\n") == []

"""DEV-92 — the cockpit's backend carries no dead code: no function, class, method or property nobody calls.

12.10.2026 the Director asked for a complete check that removes the old, unneeded „dead“ code of the cockpit. The
cleanup removed what nobody called; this keeps new dead code from coming back.

vulture reads the backend together with its tests, the migrations and the scripts — a function only a test calls counts
as used (removing it is a decision of its own). What a library calls by itself is not dead: FastAPI routes, pydantic
validators and SQLAlchemy listeners are called through their decorators, a standard-library request handler through its
method names. Variables and attributes are not judged: pydantic fields and model columns are read by the libraries.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import vulture

from backend.services import dead_code as rules

ROOT = Path(__file__).resolve().parents[1]

#: Where code lives and where it is called from.
SCANNED = ["backend", "tests", "conftest.py", "migrations", "scripts"]

#: What a framework calls, what is judged and from what confidence — the same rules the cockpit applies to every
#: project it builds (DEV-156), kept in one place.
CALLED_BY_DECORATOR = rules.CALLED_BY_DECORATOR
CALLED_BY_NAME = rules.CALLED_BY_NAME
JUDGED = rules.JUDGED
MIN_CONFIDENCE = rules.MIN_CONFIDENCE


#: DEV-155 — where production code lives. What only the tests call is not used: it is code to keep for nothing.
PRODUCTION = ["backend", "migrations", "scripts"]
#: Not production: the backend's own tests and its test-support module (``backend/testing.py``).
NOT_PRODUCTION = ["*/backend/tests/*", "*/backend/testing.py"]
#: Code that exists for the tests says so at its definition (``backend/core/test_seam.py``), not on a list here.
FOR_TESTS = "@for_tests"


def _defines_code(tree: ast.Module) -> bool:
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in tree.body)


def _declares_all(tree: ast.Module) -> bool:
    return any(
        isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "__all__" for t in n.targets) for n in tree.body
    )


def production_modules() -> list[Path]:
    return [
        f
        for p in PRODUCTION
        for f in sorted((ROOT / p).rglob("*.py"))
        if "tests" not in f.relative_to(ROOT).parts and f.name != "testing.py"
    ]


def re_export_modules(files: list[Path] | None = None) -> list[str]:
    """Modules that only re-export — imports and ``__all__``, no definition of their own. A re-export is not a use:
    vulture counts every name in ``__all__`` as used, so scanning them would keep anything they list alive."""
    return [
        str(f)
        for f in (production_modules() if files is None else files)
        if not _defines_code(t := ast.parse(f.read_text(encoding="utf-8"))) and _declares_all(t)
    ]


def dead_code(paths: list[Path], *, exclude: list[str] = [], decorators: list[str] = []) -> list[str]:  # noqa: B006
    """``file:line kind name`` of every judged piece of code nobody calls."""
    scan = vulture.Vulture(ignore_names=CALLED_BY_NAME, ignore_decorators=CALLED_BY_DECORATOR + decorators)
    scan.scavenge([str(p) for p in paths], exclude=exclude)
    found = []
    for item in scan.get_unused_code(min_confidence=MIN_CONFIDENCE):
        if item.typ not in JUDGED:
            continue
        where = Path(item.filename)
        where = where.relative_to(ROOT) if where.is_relative_to(ROOT) else where
        found.append(f"{where}:{item.first_lineno} {item.typ} {item.name}")
    return sorted(found)


def test_the_backend_has_no_dead_code():
    found = dead_code([ROOT / p for p in SCANNED])

    assert found == [], "nobody calls these — remove them, or name what calls them here:\n  " + "\n  ".join(found)


def test_the_check_finds_dead_code_and_leaves_called_code(tmp_path):
    """⚠️ The guard over the guard: a check that never finds anything is green in a burning house."""
    (tmp_path / "module.py").write_text(
        textwrap.dedent(
            """
            from fastapi import APIRouter

            router = APIRouter()


            def used():
                return 1


            def forgotten():
                return used()


            @router.get("/x")
            def route():
                return used()
            """
        ),
        encoding="utf-8",
    )

    found = dead_code([tmp_path])

    assert [line.split(" ", 1)[1] for line in found] == ["function forgotten"]


def test_no_backend_code_lives_only_for_its_tests():
    """DEV-155 (22.10.2026): 30 functions of the backend were called by their tests and nothing else — some tested a
    twin production had replaced, so the guard guarded nothing. vulture judges by name: a function whose name production
    calls elsewhere (``create``, ``update``) escapes it — what it does find is certain."""
    found = dead_code(
        [ROOT / p for p in PRODUCTION], exclude=NOT_PRODUCTION + re_export_modules(), decorators=[FOR_TESTS]
    )

    assert found == [], "only the tests call these — remove them, or mark a seam for the tests @for_tests:\n  " + (
        "\n  ".join(found)
    )


def test_the_check_finds_code_only_the_tests_call(tmp_path):
    """⚠️ The guard over the guard: production code that only a test calls is found; a marked seam is not."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "module.py").write_text(
        textwrap.dedent(
            """
            from backend.core.test_seam import for_tests


            def used():
                return 1


            def only_tested():
                return 2


            @for_tests
            def forget_everything():
                return 3
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "app" / "caller.py").write_text("from module import used\n\nused()\n", encoding="utf-8")
    # a package that only re-exports keeps nothing alive
    (tmp_path / "app" / "__init__.py").write_text(
        'from module import only_tested\n\n__all__ = ["only_tested"]\n', encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_module.py").write_text(
        "from module import only_tested, forget_everything\n\nonly_tested()\nforget_everything()\n", encoding="utf-8"
    )

    app = sorted((tmp_path / "app").glob("*.py"))
    found = dead_code([tmp_path / "app"], exclude=["*/tests/*"] + re_export_modules(app), decorators=[FOR_TESTS])

    assert [line.split(" ", 1)[1] for line in found] == ["function only_tested"]


def test_a_module_with_code_of_its_own_declares_no___all__():
    """DEV-155: vulture counts every name in ``__all__`` as used, so a module listing its own functions there hid them
    from the guard (``schemas/task.py`` kept two dead schemas alive). Nothing imports with ``*``; only re-export
    modules may list names."""
    offenders = [
        str(f.relative_to(ROOT))
        for f in production_modules()
        if _defines_code(t := ast.parse(f.read_text())) and _declares_all(t)
    ]

    assert offenders == [], "these list their own names in __all__ — remove it:\n  " + "\n  ".join(offenders)

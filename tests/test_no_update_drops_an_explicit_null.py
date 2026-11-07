"""DEV-323 — no update throws away a null that was sent.

The rule, not a list: every place in the backend that writes a field with ``setattr`` under a condition
``<value> is not None`` reads a sent null as "leave it", so an emptied optional field never gets cleared (the
surname „Test“ the Director could not remove). Such a place either uses ``backend.services.patch.apply_changes``
or says why it keeps the value: a comment starting ``# keeps a value on null: <reason>`` in the comment block right
above the condition.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
REASON = "# keeps a value on null:"


def _drops_null(node: ast.If) -> str | None:
    """The name tested ``is not None`` when the body writes it with setattr, else None."""
    tested = {
        cmp.left.id
        for cmp in ast.walk(node.test)
        if isinstance(cmp, ast.Compare)
        and isinstance(cmp.left, ast.Name)
        and any(isinstance(op, ast.IsNot) for op in cmp.ops)
        and any(isinstance(c, ast.Constant) and c.value is None for c in cmp.comparators)
    }
    for stmt in node.body:
        for call in ast.walk(stmt):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "setattr"
                and len(call.args) == 3
                and isinstance(call.args[2], ast.Name)
                and call.args[2].id in tested
            ):
                return call.args[2].id
    return None


def _places() -> list[str]:
    found = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.If) and _drops_null(node):
                block, i = [], node.lineno - 2
                while i >= 0 and lines[i].strip().startswith("#"):
                    block.append(lines[i].strip())
                    i -= 1
                if not any(line.startswith(REASON) for line in block):
                    found.append(f"{path.relative_to(BACKEND.parent)}:{node.lineno}")
    return found


def test_no_update_drops_a_null_without_a_reason() -> None:
    assert _places() == [], (
        "These updates read a sent null as 'leave it' — use backend.services.patch.apply_changes, "
        f"or write '{REASON} <why>' above the condition: {_places()}"
    )


def test_the_guard_sees_the_shape_it_looks_for() -> None:
    sample = ast.parse(
        "for field, value in data.items():\n"
        "    if field in allowed and value is not None:\n"
        "        setattr(o, field, value)\n"
    )
    ifs = [n for n in ast.walk(sample) if isinstance(n, ast.If)]
    assert [_drops_null(n) for n in ifs] == ["value"]
    clean = ast.parse("if value is not None:\n    print(value)\n")
    assert [_drops_null(n) for n in ast.walk(clean) if isinstance(n, ast.If)] == [None]

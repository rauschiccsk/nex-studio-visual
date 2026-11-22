"""DEV-488 — no module of the backend defines the same function or class twice at its top level.

22.11.2026: the fix of the deploy added ``_reads_env_file`` to ``uat_provisioner`` — a module that already had one,
used elsewhere. The second definition silently replaced the first for every caller, and neither ruff (its first use
came before the redefinition, so F811 kept quiet) nor 1 223 tests noticed; it was seen by reading the module. A name
defined twice is never meant: one of them is dead, and which one is decided by the order of the lines.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"


def test_no_module_defines_a_function_or_class_twice():
    twice: list[str] = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "tests" in path.parts:
            continue
        seen: dict[str, int] = {}
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in seen:
                    where = path.relative_to(BACKEND.parent)
                    twice.append(f"{where}:{node.lineno} {node.name} (first at line {seen[node.name]})")
                seen.setdefault(node.name, node.lineno)

    assert twice == []

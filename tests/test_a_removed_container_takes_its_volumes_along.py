"""DEV-172 — a container the cockpit removes takes its unnamed volumes along.

The build databases run ``postgres:16-alpine``, whose image declares ``VOLUME /var/lib/postgresql/data``: Docker gives
every such container an unnamed volume, whatever ``docker run`` says. The cockpit removed them with ``docker rm -f``,
which leaves that volume behind — measured 21.10.2026 on ANDROS: 1 558 unnamed volumes, 104,9 GB, 39 of 40 sampled
holding PostgreSQL data, more every month. Director: „Súhlasím“ — „Poradie nechám na teba“. Every removal of a
container in the backend says ``-v``; this test finds them all, not a list of them.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"


def _container_removals(path: Path) -> list[tuple[int, list[str]]]:
    """Every ``docker rm`` / ``docker container rm`` command written in *path*, with its words."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.List, ast.Tuple)):
            items = node.elts
        elif isinstance(node, ast.Call):
            items = node.args
        else:
            continue
        words = [item.value for item in items if isinstance(item, ast.Constant) and isinstance(item.value, str)]
        if "docker" in words and "rm" in words and not {"network", "volume", "image"} & set(words):
            found.append((node.lineno, words))
    return found


def test_every_container_the_cockpit_removes_goes_with_its_volumes():
    removals = {
        f"{path.relative_to(BACKEND)}:{line}": words
        for path in sorted(BACKEND.rglob("*.py"))
        if "tests" not in path.relative_to(BACKEND).parts
        for line, words in _container_removals(path)
    }

    assert removals, "the search found no container removal at all — it no longer sees how the backend writes them"
    without_volumes = {where: words for where, words in removals.items() if "-v" not in words}
    assert without_volumes == {}, f"a container removed without its unnamed volumes: {without_volumes}"

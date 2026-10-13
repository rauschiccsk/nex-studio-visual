"""DEV-109 — deleting a project removes its preview too: the container ``vizual-<slug>`` and the route it carries.

13.10.2026: the delete route tore down the project's UAT, CI runner, knowledge base folder, search index, workspace
and (on request) its GitHub repository — but not the preview. The function that removes it existed and nothing outside
the tests called it, so a deleted project's frontend kept running at ``vizual-<slug>.isnex.eu``. The removal is
best-effort like every cleanup beside it: a container still running afterwards is written to the server log.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from backend.services import vizual_sandbox
from tests.test_project_router import _payload, creator, router_client, uat_root  # noqa: F401 — fixtures

# The same isolation the router suite opts into: every create runs against a temporary knowledge base and workspace.
pytestmark = pytest.mark.usefixtures("_isolate_create_project_kb")

BACKEND = Path(__file__).resolve().parents[1] / "backend"


def _create(client, owner) -> dict:
    created = client.post("/api/v1/projects", json=_payload(owner.id))
    assert created.status_code == 201, created.text
    return created.json()


def test_deleting_a_project_removes_its_preview(router_client, creator, monkeypatch):  # noqa: F811
    removed: list[str] = []
    monkeypatch.setattr(vizual_sandbox, "_force_remove", removed.append)
    monkeypatch.setattr(vizual_sandbox, "_inspect_state", lambda name: {"running": False})
    project = _create(router_client, creator)

    assert router_client.delete(f"/api/v1/projects/{project['id']}").status_code == 204

    assert removed == [f"vizual-{project['slug']}"]


def test_a_preview_that_survives_the_removal_is_written_to_the_log(router_client, creator, monkeypatch, caplog):  # noqa: F811
    monkeypatch.setattr(vizual_sandbox, "_force_remove", lambda name: None)
    monkeypatch.setattr(vizual_sandbox, "_inspect_state", lambda name: {"running": True})
    # backend.main stops the backend loggers from propagating; let this one reach caplog for the test.
    monkeypatch.setattr(logging.getLogger("backend"), "propagate", True)
    project = _create(router_client, creator)

    with caplog.at_level(logging.WARNING):
        response = router_client.delete(f"/api/v1/projects/{project['id']}")

    assert response.status_code == 204, "a cleanup that failed must not undo the delete"
    said = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(f"vizual-{project['slug']}" in line and "still runs" in line for line in said), said


def test_the_preview_removal_has_a_caller_outside_the_tests():
    """The bug was a removal nobody called — a guard on the call, so it cannot quietly lose its caller again."""
    callers = []
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.relative_to(BACKEND).parts or path.name == "vizual_sandbox.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "teardown"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "vizual_sandbox"
            ):
                callers.append(f"{path.relative_to(BACKEND)}:{node.lineno}")

    assert callers, "nothing outside the tests removes a project's preview"

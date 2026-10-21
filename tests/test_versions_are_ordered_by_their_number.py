"""DEV-169 — versions are ordered by their number, the same way on every screen.

The project page ordered versions as text: Career Asistent showed v1.0.0 above 1.1.0 (the ``v`` of the first PROD
version sorts after every digit) and NEX Inbox showed 1.10.0 below 1.5.0. The numeric order lived only in the deploy
dropdown; the version list and the cumulative costs (Náklady) still compared strings. Director 21.10.2026: „poradie
nejako nesedí“ — „Áno založ.“
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.main import app
from backend.services import metrics
from backend.services import version as version_service

BACKEND = Path(__file__).resolve().parents[1] / "backend"


def _project(db, numbers: list[str]) -> Project:
    name = f"u-{uuid.uuid4().hex[:8]}"
    owner = User(username=name, email=f"{name}@example.com", password_hash="x", role="ri", is_active=True)
    db.add(owner)
    db.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:6]}",
        slug=f"p-{uuid.uuid4().hex[:6]}",
        type="standard",
        auth_mode="password",
        description="",
        created_by=owner.id,
    )
    db.add(project)
    db.flush()
    for number in numbers:
        db.add(Version(project_id=project.id, version_number=number, name=number))
        db.flush()
    return project


@pytest.mark.parametrize(
    ("founded", "shown"),
    [
        (["0.1.0", "v1.0.0", "1.1.0"], ["1.1.0", "v1.0.0", "0.1.0"]),
        (["1.5.0", "1.9.0", "1.10.0"], ["1.10.0", "1.9.0", "1.5.0"]),
    ],
)
def test_the_project_page_lists_the_newest_number_first(client, db_session, founded, shown):
    project = _project(db_session, founded)
    app.dependency_overrides[get_current_user] = lambda: db_session.get(User, project.created_by)

    answer = client.get(f"/api/v1/projects/{project.id}/versions")

    assert answer.status_code == 200, answer.text
    assert [v["version_number"] for v in answer.json()] == shown
    assert [v.version_number for v in version_service.list_versions(db_session, project.id)] == shown


def test_the_costs_add_the_versions_up_in_the_order_of_their_numbers(db_session):
    project = _project(db_session, ["1.10.0", "v1.0.0", "1.9.0", "0.1.0"])

    costs = metrics.compute_project_metrics(db_session, project)

    assert [v.version_number for v in costs.by_version] == ["0.1.0", "v1.0.0", "1.9.0", "1.10.0"]


def _ordered_as_text(path: Path) -> list[int]:
    """Lines of *path* that let the database order by ``version_number`` — the order of text, not of numbers."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "order_by":
            if any("version_number" in ast.unparse(arg) for arg in node.args):
                found.append(node.lineno)
    return found


def test_no_version_list_is_ordered_as_text():
    found = {
        str(path.relative_to(BACKEND)): lines
        for path in BACKEND.rglob("*.py")
        if "tests" not in path.relative_to(BACKEND).parts and (lines := _ordered_as_text(path))
    }

    assert found == {}, f"ordered as text, not by number — use version.number_order_key: {found}"

"""DEV-150 — the Prehľad says where each project stands: its open version, phase, tasks, who is on turn since when.

Director 20.10.2026: „V NSV v záložke prehľad by som privítal informciu o tom, ako stojíme s daným projektom. Aby v
prípade, že vývoj projektu z nejakej príčiny nedokončíme (príde niečo dôležitejšie) mali by sme presnú informáciu o
tom, kde a ako pokračovať v projekte.“ NEX Manager 1.3.0 had waited on a question of the agent since 15.10.2026 and
the Prehľad showed its name only. Only the cockpit's records speak — never the agent's text.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from backend.core.authz import ADMIN_USERNAME
from backend.core.security import get_current_user
from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.main import app
from backend.services import standing

AGENTS_QUESTION = "Má sa limit počítať podľa IP alebo podľa účtu?"


def _user(db, username=None) -> User:
    name = username or f"u-{uuid.uuid4().hex[:8]}"
    user = User(username=name, email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    return user


def _project(db, owner: User, name: str) -> Project:
    project = Project(
        name=name,
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=owner.id,
    )
    db.add(project)
    db.flush()
    return project


def _version(db, project, number, status, *, stage="programovanie", block_reason=None, since=None, tasks=(0, 0)):
    version = Version(project_id=project.id, version_number=number)
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage=stage,
        current_actor="ai_agent",
        status=status,
        block_reason=block_reason,
        next_action=f"AI Agent (úloha #1.1.3) sa pýta: {AGENTS_QUESTION}",
    )
    db.add(state)
    db.flush()
    if since is not None:
        state.awaiting_director_since = since
    done, total = tasks
    if total:
        epic = Epic(project_id=project.id, version_id=version.id, number=1, title="E", status="planned")
        db.add(epic)
        db.flush()
        feat = Feat(epic_id=epic.id, number=1, title="F", status="todo")
        db.add(feat)
        db.flush()
        for i in range(total):
            db.add(
                Task(
                    feat_id=feat.id,
                    number=i + 1,
                    title=f"T{i}",
                    task_type="backend",
                    status="done" if i < done else "todo",
                )
            )
    db.flush()
    return version


def _deploy(db, project, number, status):
    customer = Customer(project_id=project.id, name="ICC", slug=f"c{uuid.uuid4().hex[:6]}")
    db.add(customer)
    db.flush()
    db.add(
        DeployEvent(
            customer_id=customer.id,
            project_id=project.id,
            version_number=number,
            environment="prod",
            event_type="deploy",
            status=status,
        )
    )
    db.flush()


@pytest.fixture
def world(db_session):
    db = db_session
    me, someone = _user(db), _user(db)
    manager = _project(db, me, "NEX Manager")
    _version(
        db, manager, "1.3.0", "awaiting_manazer", since=datetime(2026, 10, 15, 9, tzinfo=timezone.utc), tasks=(7, 16)
    )
    inbox = _project(db, me, "NEX Inbox")
    _version(
        db,
        inbox,
        "1.10.0",
        "blocked",
        block_reason="agent_question",
        since=datetime(2026, 10, 18, 9, tzinfo=timezone.utc),
    )
    career = _project(db, me, "Career Asistent")
    _version(db, career, "1.2.0", "agent_working", tasks=(2, 5))
    websites = _project(db, me, "NEX Websites")
    _version(db, websites, "0.3.0", "paused", stage="navrh")
    done = _project(db, me, "NEX Genesis")
    _version(db, done, "1.2.2", "done", stage="done")
    _deploy(db, done, "1.2.2", "ok")
    _deploy(db, done, "1.2.3", "failed")
    _project(db, someone, "Cudzí projekt")
    return db, me


def _shape(projects):
    return [
        (
            p.name,
            [
                (v.version_number, v.stage, v.status, v.block_reason, v.waiting_since, v.tasks_done, v.tasks_total)
                for v in p.open_versions
            ],
            p.prod_version,
        )
        for p in projects
    ]


def test_each_project_says_where_it_stands_those_waiting_on_a_person_first(world):
    db, me = world

    shown = [
        p
        for p in standing.project_standing(db, me)
        if p.name in {"NEX Manager", "NEX Inbox", "Career Asistent", "NEX Websites", "NEX Genesis"}
    ]

    assert _shape(shown) == [
        (
            "NEX Manager",
            [
                (
                    "1.3.0",
                    "programovanie",
                    "awaiting_manazer",
                    None,
                    datetime(2026, 10, 15, 9, tzinfo=timezone.utc),
                    7,
                    16,
                )
            ],
            None,
        ),
        (
            "NEX Inbox",
            [
                (
                    "1.10.0",
                    "programovanie",
                    "blocked",
                    "agent_question",
                    datetime(2026, 10, 18, 9, tzinfo=timezone.utc),
                    0,
                    0,
                )
            ],
            None,
        ),
        ("Career Asistent", [("1.2.0", "programovanie", "agent_working", None, None, 2, 5)], None),
        ("NEX Websites", [("0.3.0", "navrh", "paused", None, None, 0, 0)], None),
        ("NEX Genesis", [], "1.2.2"),
    ]


def test_only_the_projects_the_person_works_on_and_never_the_agents_text(world):
    db, me = world

    shown = standing.project_standing(db, me)

    assert "Cudzí projekt" not in [p.name for p in shown]
    assert AGENTS_QUESTION not in str([p.model_dump() for p in shown])


def test_the_admin_sees_every_project(world):
    db, _me = world
    admin = db.query(User).filter(User.username == ADMIN_USERNAME).one_or_none() or _user(db, ADMIN_USERNAME)

    assert "Cudzí projekt" in [p.name for p in standing.project_standing(db, admin)]


def test_the_overview_asks_the_route(client, world):
    _db, me = world
    app.dependency_overrides[get_current_user] = lambda: me

    answer = client.get("/api/v1/standing")

    assert answer.status_code == 200, answer.text
    first = answer.json()["projects"][0]
    assert (first["name"], first["open_versions"][0]["version_number"], first["open_versions"][0]["tasks_done"]) == (
        "NEX Manager",
        "1.3.0",
        7,
    )

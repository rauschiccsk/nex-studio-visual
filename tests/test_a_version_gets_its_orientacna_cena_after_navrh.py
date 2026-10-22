"""DEV-154 — a version gets its orientačná cena after Návrh, from the number of tasks in its plan.

Director 20.10.2026: „Jedná sa skutočne len o odhad, aby zákazník vedel sa rozhodnúť, či objedná daný projekt.“ Measured
22.10.2026 on 15 finished versions: the tasks of the plan predict the delivered tokens (22 % off on average), the size
of the Špecifikácia does not (39 %, worst 126 %). Director: „Súhlasím“ — after Návrh, from the plan. A project's first
version is measured apart; a fix of our error gets none; the estimate is kept and shown beside the statement.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.price_estimate import PriceSample
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.main import app
from backend.services import delivered_tokens, delivery_statement, price_estimate

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _owner(db) -> User:
    user = User(
        username=f"u-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@e.sk", password_hash="x", role="ri"
    )
    db.add(user)
    db.flush()
    return user


def _project(db, owner, name) -> Project:
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


def _version(db, project, number, *, done, tasks=0, day=0, flow="new_version", work_kind=None) -> Version:
    version = Version(
        project_id=project.id,
        version_number=number,
        rate_code=Decimal("2"),
        rate_docs=Decimal("1"),
        work_kind=work_kind,
        created_at=T0 + timedelta(days=day),
    )
    db.add(version)
    db.flush()
    db.add(
        PipelineState(
            version_id=version.id,
            flow_type=flow,
            current_stage="done" if done else "navrh",
            current_actor="ai_agent",
            status="done" if done else "awaiting_manazer",
            next_action="x",
        )
    )
    if tasks:
        number = db.query(Epic).filter(Epic.project_id == project.id).count() + 1
        epic = Epic(project_id=project.id, version_id=version.id, number=number, title="E", status="planned")
        db.add(epic)
        db.flush()
        feat = Feat(epic_id=epic.id, number=1, title="F", status="todo")
        db.add(feat)
        db.flush()
        for i in range(tasks):
            db.add(Task(feat_id=feat.id, number=i + 1, title=f"T{i}", task_type="backend", status="todo"))
    db.flush()
    return version


def _sample(db, version, *, first, tasks, code, docs):
    db.add(PriceSample(version_id=version.id, first_version=first, tasks=tasks, code_tokens=code, docs_tokens=docs))
    db.flush()


@pytest.fixture
def measured(db_session):
    """One first version and three later ones measured — per task: code 5 000/4 000/6 000, docs 1 000/1 500/1 000."""
    db = db_session
    owner = _owner(db)
    inbox = _project(db, owner, f"NEX Inbox {uuid.uuid4().hex[:4]}")
    first = _version(db, inbox, "1.0.0", done=True, day=0)
    a = _version(db, inbox, "1.1.0", done=True, day=1)
    b = _version(db, inbox, "1.2.0", done=True, day=2)
    c = _version(db, inbox, "1.3.0", done=True, day=3)
    _sample(db, first, first=True, tasks=100, code=900_000, docs=50_000)
    _sample(db, a, first=False, tasks=10, code=50_000, docs=10_000)
    _sample(db, b, first=False, tasks=20, code=80_000, docs=30_000)
    _sample(db, c, first=False, tasks=5, code=30_000, docs=5_000)
    return db, owner, inbox


def test_a_later_version_is_estimated_from_the_later_versions_tasks(measured):
    db, _owner_, inbox = measured
    version = _version(db, inbox, "1.4.0", done=False, tasks=10, day=4)

    found = price_estimate.figures(db, version)

    assert (found.first_version, found.tasks, found.samples) == (False, 10, 3)
    assert (found.code_tokens, found.docs_tokens) == (50_000, 11_667)
    assert found.amount_eur == Decimal("111.67")  # 50 000 × 2 € + 11 667 × 1 € per 1 000 tokens
    assert found.spread_pct == 12  # per task 6 000, 5 500, 7 000 — stdev 764 of mean 6 167


def test_the_first_version_of_a_new_project_is_estimated_from_first_versions(measured):
    db, owner, _inbox = measured
    new = _project(db, owner, f"Nová zákazka {uuid.uuid4().hex[:4]}")
    version = _version(db, new, "0.1.0", done=False, tasks=50, day=5)

    found = price_estimate.figures(db, version)

    assert (found.first_version, found.samples, found.spread_pct) == (True, 1, None)
    assert (found.code_tokens, found.docs_tokens) == (450_000, 25_000)


def test_no_estimate_for_a_fix_of_our_error_nor_before_the_plan(measured):
    db, _owner_, inbox = measured
    fix = _version(db, inbox, "1.3.1", done=False, tasks=3, day=4, flow="fast_fix", work_kind="fix")
    unplanned = _version(db, inbox, "1.5.0", done=False, tasks=0, day=5)

    assert price_estimate.figures(db, fix) is None
    assert price_estimate.figures(db, unplanned) is None


def test_the_estimate_is_kept_and_measured_anew_when_the_plan_changes(measured, monkeypatch, tmp_path):
    db, _owner_, inbox = measured
    monkeypatch.setattr(price_estimate, "refresh_samples", lambda db, root: 0)
    version = _version(db, inbox, "1.4.0", done=False, tasks=10, day=4)

    first = price_estimate.current(db, version, tmp_path)
    kept = (first.id, first.created_at, first.tasks, first.amount_eur)
    again = price_estimate.current(db, version, tmp_path)
    kept_again = (again.id, again.created_at)
    feat = db.query(Feat).join(Epic, Epic.id == Feat.epic_id).filter(Epic.version_id == version.id).one()
    db.add(Task(feat_id=feat.id, number=11, title="T10", task_type="backend", status="todo"))
    db.flush()
    larger = price_estimate.current(db, version, tmp_path)

    assert kept_again == kept[:2]
    assert (kept[2], larger.tasks) == (10, 11) and larger.amount_eur > kept[3]
    assert larger.created_at > kept[1]


def test_a_finished_version_becomes_a_measurement_once(db_session, monkeypatch, tmp_path):
    db = db_session
    owner = _owner(db)
    project = _project(db, owner, f"NEX Manager {uuid.uuid4().hex[:4]}")
    v1 = _version(db, project, "1.0.0", done=True, tasks=4, day=0)
    v2 = _version(db, project, "1.1.0", done=True, tasks=2, day=1)
    counted: list[str] = []

    class _Count:
        def tokens(self, kind):
            return {delivered_tokens.KIND_CODE: 700, delivered_tokens.KIND_TESTS: 300, delivered_tokens.KIND_DOCS: 200}[
                kind
            ]

    def _preview(db, version, repo):
        counted.append(version.version_number)
        return delivery_statement.Preview(version_number=version.version_number, count=_Count())

    monkeypatch.setattr(delivery_statement, "preview", _preview)

    added = price_estimate.refresh_samples(db, tmp_path)
    again = price_estimate.refresh_samples(db, tmp_path)

    samples = {s.version_id: s for s in db.query(PriceSample).filter(PriceSample.version_id.in_([v1.id, v2.id]))}
    assert (added, again) == (2, 0) and sorted(counted) == ["1.0.0", "1.1.0"]
    assert (samples[v1.id].first_version, samples[v2.id].first_version) == (True, False)
    assert (samples[v1.id].tasks, samples[v1.id].code_tokens, samples[v1.id].docs_tokens) == (4, 1000, 200)


def test_the_route_gives_the_estimate_and_says_when_it_rests_on_too_few(client, measured, monkeypatch):
    db, owner, inbox = measured
    monkeypatch.setattr(price_estimate, "refresh_samples", lambda db, root: 0)
    version = _version(db, inbox, "1.4.0", done=False, tasks=10, day=4)
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: owner

    body = client.get(f"/api/v1/versions/{version.id}/price-estimate").json()

    assert (body["amount_eur"], body["code_eur"], body["docs_eur"]) == ("111.67", "100.00", "11.67")
    assert (body["samples"], body["enough"], body["spread_pct"], body["tasks"]) == (3, False, 12, 10)


def test_the_billing_tab_shows_the_estimate_beside_the_statement(client, measured, monkeypatch):
    db, owner, inbox = measured
    monkeypatch.setattr(price_estimate, "refresh_samples", lambda db, root: 0)
    version = _version(db, inbox, "1.4.0", done=False, tasks=10, day=4)
    price_estimate.current(db, version, Path("/nonexistent"))
    monkeypatch.setattr(
        delivery_statement,
        "billing_rows",
        lambda db, project, repo: [
            delivery_statement.BillingRow(
                version=version,
                flow_type="new_version",
                preview=delivery_statement.Preview(version_number="1.4.0"),
                valid=None,
                state="not_done",
            )
        ],
    )
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: owner

    row = client.get(f"/api/v1/projects/{inbox.slug}/billing").json()["rows"][0]

    assert (row["estimate_amount_eur"], row["estimate_spread_pct"]) == ("111.67", 12)


def test_someone_who_may_not_work_on_the_project_gets_no_estimate(client, measured, monkeypatch):
    db, _owner_, inbox = measured
    monkeypatch.setattr(price_estimate, "refresh_samples", lambda db, root: 0)
    version = _version(db, inbox, "1.4.0", done=False, tasks=10, day=4)
    stranger = _owner(db)
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: stranger

    response = client.get(f"/api/v1/versions/{version.id}/price-estimate")

    assert response.status_code in (403, 404)
    assert price_estimate.stored(db, version.id) is None

"""DEV-82 — the widths of the side panels and the collapsed sidebar are kept on the server, for every person apart.

12.10.2026 the Director wanted to change the widths of the side panels, kept per user:
„Treba aby systém zapamätal tieto nastavenia pre každého užívateľa osobitne.“
Kept on the server, so a person has the same layout on every computer; another person never sees it; a width outside
its bounds is refused, not stored.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import DBAPIError

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.models.user_layout import UserLayout
from backend.main import app
from backend.services import user_layout

URL = "/api/v1/user-layout"


def _user(db, name):
    suffix = uuid.uuid4().hex[:8]
    user = User(username=f"{name}_{suffix}", email=f"{name}_{suffix}@t.sk", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def people(db_session):
    return db_session, _user(db_session, "maria"), _user(db_session, "tibor")


def _as(db, user):
    user_id = user.id
    app.dependency_overrides[get_current_user] = lambda: db.get(User, user_id)


def test_nothing_chosen_is_the_default_with_the_bounds(client, people):
    db, maria, _tibor = people
    _as(db, maria)

    body = client.get(URL).json()

    assert (body["sidebar_width"], body["sidebar_collapsed"], body["plan_width"]) == (None, False, None)
    assert body["limits"] == {
        "sidebar_width": {"min": 180, "max": 360, "default": 224},
        "plan_width": {"min": 280, "max": 640, "default": 320},
    }


def test_a_width_is_kept_on_the_server_and_another_person_does_not_have_it(client, people):
    db, maria, tibor = people
    _as(db, maria)
    assert client.put(URL, json={"sidebar_width": 300, "sidebar_collapsed": True}).status_code == 200

    # Another computer: only the server knows — a fresh read of the stored row.
    db.expire_all()
    again = client.get(URL).json()
    assert (again["sidebar_width"], again["sidebar_collapsed"], again["plan_width"]) == (300, True, None)

    _as(db, tibor)
    other = client.get(URL).json()
    assert (other["sidebar_width"], other["sidebar_collapsed"]) == (None, False)


def test_only_what_is_sent_changes_and_an_empty_width_returns_to_the_default(client, people):
    db, maria, _tibor = people
    _as(db, maria)
    client.put(URL, json={"sidebar_width": 300, "plan_width": 500, "sidebar_collapsed": True})

    assert client.put(URL, json={"plan_width": 400}).json()["sidebar_width"] == 300
    body = client.put(URL, json={"sidebar_width": None}).json()

    assert (body["sidebar_width"], body["plan_width"], body["sidebar_collapsed"]) == (None, 400, True)
    assert client.put(URL, json={}).json()["plan_width"] == 400


@pytest.mark.parametrize(
    ("field", "value"), [("sidebar_width", 179), ("sidebar_width", 361), ("plan_width", 279), ("plan_width", 641)]
)
def test_a_width_outside_its_bounds_is_refused_and_nothing_is_stored(client, people, field, value):
    db, maria, _tibor = people
    _as(db, maria)
    client.put(URL, json={"sidebar_width": 200, "plan_width": 300})

    response = client.put(URL, json={field: value})

    assert response.status_code == 422
    assert "musí byť od" in response.json()["detail"]
    body = client.get(URL).json()
    assert (body["sidebar_width"], body["plan_width"]) == (200, 300)


@pytest.mark.parametrize(("field", "value"), [("sidebar_width", 400), ("plan_width", 100)])
def test_the_database_refuses_a_width_the_service_would_not_store(db_session, field, value):
    maria = _user(db_session, "maria")
    with pytest.raises(DBAPIError, match=f"ck_user_layouts_{field}"):
        with db_session.begin_nested():
            db_session.add(UserLayout(user_id=maria.id, sidebar_collapsed=False, **{field: value}))
            db_session.flush()


def test_the_bounds_the_screen_reads_are_the_ones_the_database_checks():
    """One source of the numbers: the service; the migration and the model check the same."""
    from backend.db.models.user_layout import UserLayout as model

    checks = {c.name: str(c.sqltext) for c in model.__table__.constraints if c.name and c.name.startswith("ck_")}
    for key, limit in user_layout.LIMITS.items():
        assert checks[f"ck_user_layouts_{key}"] == f"{key} BETWEEN {limit.min} AND {limit.max}"


def test_without_signing_in_there_is_no_layout(client):
    app.dependency_overrides.pop(get_current_user, None)
    assert client.get(URL).status_code == 401

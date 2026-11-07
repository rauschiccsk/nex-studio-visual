"""DEV-320 — every cockpit account has its own colour, so windows of different accounts side by side are not mixed up.

Director 07.11.2026: „aby som mohol paralelne púšťať viac kokpitov … aby pod jedným prihlásením spúšťať NSV pod
rôznymi používateľmi“ — each account runs in its own Chrome profile, and the app window does not show the profile's
colour, so the cockpit shows the account's. A new account takes the next colour of the palette by itself; the person
changes it in Nastavenia, only to a colour of the palette; the palette is the server's (one source).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.api.routes import auth as auth_routes
from backend.config.settings import ACCOUNT_COLORS
from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.user import UserCreate
from backend.services import user as user_service


def _new(db: Any) -> User:
    name = f"u_{uuid.uuid4().hex[:8]}"
    payload = UserCreate(username=name, email=f"{name}@example.com", password="heslo123", role="ri")
    return user_service.create(db, payload)


def _client(db: Any, user: User) -> TestClient:
    app = FastAPI()
    app.include_router(auth_routes.router, prefix="/api/v1/auth")

    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_new_accounts_take_the_palette_in_turn(db_session):
    before = db_session.execute(select(func.count()).select_from(User)).scalar_one()
    colours = [_new(db_session).accent_color for _ in range(3)]
    assert colours == [ACCOUNT_COLORS[(before + i) % len(ACCOUNT_COLORS)] for i in range(3)]


def test_me_carries_the_colour_and_the_person_changes_it(db_session):
    me = _new(db_session)
    c = _client(db_session, me)
    assert c.get("/api/v1/auth/me").json()["accent_color"] == me.accent_color

    other = next(colour for colour in ACCOUNT_COLORS if colour != me.accent_color)
    r = c.patch("/api/v1/auth/me", json={"accent_color": other})

    assert r.status_code == 200, r.text
    assert r.json()["accent_color"] == other
    db_session.refresh(me)
    assert me.accent_color == other


def test_a_colour_outside_the_palette_is_refused(db_session):
    me = _new(db_session)
    r = _client(db_session, me).patch("/api/v1/auth/me", json={"accent_color": "#123456"})
    assert r.status_code == 422
    db_session.refresh(me)
    assert me.accent_color in ACCOUNT_COLORS


def test_the_palette_comes_from_the_server(db_session):
    r = _client(db_session, _new(db_session)).get("/api/v1/auth/accent-colors")
    assert r.status_code == 200 and r.json() == list(ACCOUNT_COLORS)
    assert len(set(ACCOUNT_COLORS)) == len(ACCOUNT_COLORS) >= 7, "seven accounts on one desktop need seven colours"

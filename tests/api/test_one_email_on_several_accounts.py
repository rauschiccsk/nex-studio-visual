"""DEV-321 — one e-mail on several accounts of the cockpit.

The Director has several accounts (zoltan, rausch, admin), all his own, and wants his e-mail on each. Sign-in is by
username, nobody is looked up by e-mail, so the e-mail need not be unique: a new account, the admin's edit in Users and
My account all take an e-mail another account already has. The username stays unique.
"""

from __future__ import annotations

import uuid

from .conftest import login_user, seed_user

SHARED = "zoltan.rausch@example.com"


def _admin(client, db_session) -> dict:
    name = f"ri_{uuid.uuid4().hex[:8]}"
    seed_user(db_session, username=name, password="Nex12345", role="ri")
    return {"Authorization": f"Bearer {login_user(client, username=name, password='Nex12345')}"}


def _account(**overrides) -> dict:
    body = {"username": f"u_{uuid.uuid4().hex[:8]}", "email": f"{uuid.uuid4().hex[:8]}@example.com"}
    body.update({"password": "SecurePass123", "role": "ha", **overrides})
    return body


def test_a_new_account_takes_an_email_another_account_has(client, db_session) -> None:
    auth = _admin(client, db_session)
    first = client.post("/api/v1/users", json=_account(email=SHARED), headers=auth)
    second = client.post("/api/v1/users", json=_account(email=SHARED), headers=auth)
    assert (first.status_code, second.status_code) == (201, 201), second.text
    assert first.json()["email"] == second.json()["email"] == SHARED
    assert first.json()["id"] != second.json()["id"]


def test_users_edit_gives_an_account_an_email_another_account_has(client, db_session) -> None:
    auth = _admin(client, db_session)
    assert client.post("/api/v1/users", json=_account(email=SHARED), headers=auth).status_code == 201
    other = client.post("/api/v1/users", json=_account(), headers=auth).json()
    resp = client.patch(f"/api/v1/users/{other['id']}", json={"email": SHARED}, headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == SHARED


def test_my_account_takes_an_email_another_account_has(client, db_session) -> None:
    auth = _admin(client, db_session)
    assert client.post("/api/v1/users", json=_account(email=SHARED), headers=auth).status_code == 201
    me = f"me_{uuid.uuid4().hex[:8]}"
    seed_user(db_session, username=me, password="Nex12345", role="shu")
    mine = {"Authorization": f"Bearer {login_user(client, username=me, password='Nex12345')}"}
    resp = client.patch("/api/v1/auth/me", json={"email": SHARED}, headers=mine)
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == SHARED


def test_a_taken_username_is_still_refused(client, db_session) -> None:
    auth = _admin(client, db_session)
    taken = f"taken_{uuid.uuid4().hex[:8]}"
    assert client.post("/api/v1/users", json=_account(username=taken), headers=auth).status_code == 201
    again = client.post("/api/v1/users", json=_account(username=taken), headers=auth)
    assert again.status_code == 409
    assert "username" in again.json()["detail"].lower()
    other = client.post("/api/v1/users", json=_account(), headers=auth).json()
    rename = client.patch(f"/api/v1/users/{other['id']}", json={"username": taken}, headers=auth)
    assert rename.status_code == 409
    assert "username" in rename.json()["detail"].lower()

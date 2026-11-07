"""DEV-323 — the Director's case end to end: Nastavenia → Používatelia → Upraviť sends the whole form, an emptied
surname as null (frontend/src/pages/SettingsPage.tsx, handleUpdateUser), and the surname is gone after the save."""

from __future__ import annotations

from .conftest import login_user, seed_user


def test_an_emptied_surname_is_saved(client, db_session) -> None:
    seed_user(db_session, username="ri_dev323", password="Nex12345", role="ri")
    auth = {"Authorization": f"Bearer {login_user(client, username='ri_dev323', password='Nex12345')}"}
    target = seed_user(db_session, username="admin_dev323", password="Nex12345", role="ri", first_name="Administrátor")
    form = {"first_name": "Administrátor", "telegram_chat_id": None, "email": target.email, "role": "ri"}
    with_surname = client.patch(f"/api/v1/users/{target.id}", json={**form, "last_name": "Test"}, headers=auth)
    assert with_surname.json()["last_name"] == "Test"

    emptied = client.patch(
        f"/api/v1/users/{target.id}", json={**form, "last_name": None, "is_active": True}, headers=auth
    )

    assert emptied.status_code == 200, emptied.text
    assert emptied.json()["last_name"] is None
    assert emptied.json()["first_name"] == "Administrátor"

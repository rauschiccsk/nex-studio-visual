"""DEV-37 — the new-project form starts with the options the Manažér always sets.

Director 09.10.2026, creating NEX Career: „V dolnej časti je "Možnosti nastavenia" vždy musíme nastavovať. Chcem
aby pri založení nového projektu už tie voľby boli prednastavené." Approved preset: CI and the full check ON;
branch protection OFF — and unavailable while GitHub refuses it for private repositories (measured 09.10.2026 on
nex-inbox, nex-manager, nex-websites: „Upgrade to GitHub Pro or make this repository public to enable this
feature"; every new repository the cockpit creates is private). When the plan changes, one switch in
Nastavenia → GitHub makes it available again — no code change.

The presets come from the backend (one place); the form takes them. The API's own defaults for a caller that
omits a flag stay as they were — the preset is what the FORM starts with.
"""

from __future__ import annotations

import pytest

from backend.services import github_validation as github_validation_service
from backend.services import system_setting as system_setting_service
from tests.api.test_project_create_validation import _payload, creator, router_client  # noqa: F401

pytestmark = pytest.mark.usefixtures("_isolate_create_project_kb")

PROTECTION = "github_private_branch_protection"
REFUSED = "Ochranu hlavnej vetvy GitHub pri súkromnom repozitári na našom pláne nedovolí"


def _protection(db_session, enabled: bool) -> None:
    system_setting_service.upsert(db_session, PROTECTION, "true" if enabled else "false")
    db_session.flush()


def test_the_form_starts_with_ci_and_the_full_check_on(router_client, db_session):  # noqa: F811
    _protection(db_session, False)

    resp = router_client.get("/api/v1/projects/create-presets")

    assert resp.status_code == 200
    assert resp.json() == {
        "enable_cicd": True,
        "full_smoke": True,
        "enable_branch_protection": False,
        "custom_development_enabled": False,
        "private_network": False,  # DEV-42: public as before unless the Manažér asks
        "branch_protection_available": False,
        "branch_protection_note": (
            f"{REFUSED} (treba GitHub Pro). Keď sa plán zmení, zapni ju v Nastavenia → GitHub → "
            "„Ochrana vetvy pri súkromnom repozitári“."
        ),
    }


def test_once_the_plan_allows_it_protection_is_offered(router_client, db_session):  # noqa: F811
    _protection(db_session, True)

    body = router_client.get("/api/v1/projects/create-presets").json()

    assert body["branch_protection_available"] is True
    assert body["branch_protection_note"] is None
    assert body["enable_branch_protection"] is False  # offered, not imposed


def test_the_switch_lives_in_nastavenia_github_and_is_off(db_session):
    default = system_setting_service.DEFAULT_SETTINGS[PROTECTION]
    assert (default.value, default.value_type) == ("false", "bool")
    assert default.label == "Ochrana vetvy pri súkromnom repozitári"


def test_a_create_asking_for_protection_it_cannot_get_is_refused_before_anything_happens(
    router_client,  # noqa: F811
    creator,  # noqa: F811
    db_session,
    monkeypatch,
):
    _protection(db_session, False)

    def _must_not_run(*args, **kwargs):
        raise AssertionError("the repository was created although the request is refused")

    monkeypatch.setattr(github_validation_service, "create_github_repo", _must_not_run)
    payload = _payload(creator.id, repo_url="rauschiccsk/nex-career", enable_branch_protection=True)

    resp = router_client.post("/api/v1/projects", json=payload)

    assert resp.status_code == 422
    assert resp.json()["detail"].startswith(REFUSED)


def test_with_the_switch_on_the_request_goes_on_to_github(router_client, creator, db_session, monkeypatch):  # noqa: F811
    _protection(db_session, True)
    reached: dict[str, bool] = {}

    def _github(*args, **kwargs):
        reached["github"] = True
        raise ValueError("GitHub v skúške neodpovedá.")

    monkeypatch.setattr(github_validation_service, "create_github_repo", _github)
    payload = _payload(creator.id, repo_url="rauschiccsk/nex-career", enable_branch_protection=True)

    resp = router_client.post("/api/v1/projects", json=payload)

    assert reached == {"github": True}
    assert REFUSED not in str(resp.json())

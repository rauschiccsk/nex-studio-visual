"""DEV-42 — „Prístup: len súkromná sieť" is set when a project is founded, changed in its settings and offered by
the new-project form switched off (projects stay public unless the Manažér asks).

The deploy and the Vizuál preview read the column (``tests/test_private_network_projects.py``); here: it is
stored, echoed back and changeable through the API the screens use.
"""

from __future__ import annotations

import pytest

from tests.api.test_project_create_validation import _payload, creator, router_client  # noqa: F401

pytestmark = pytest.mark.usefixtures("_isolate_create_project_kb")


def test_a_project_founded_private_says_so(router_client, creator):  # noqa: F811
    resp = router_client.post("/api/v1/projects", json=_payload(creator.id, private_network=True))

    assert resp.status_code == 201, resp.text
    assert resp.json()["private_network"] is True
    assert router_client.get(f"/api/v1/projects/{resp.json()['id']}").json()["private_network"] is True


def test_a_project_founded_without_the_option_stays_public(router_client, creator):  # noqa: F811
    resp = router_client.post("/api/v1/projects", json=_payload(creator.id))

    assert resp.status_code == 201, resp.text
    assert resp.json()["private_network"] is False


def test_the_option_is_changed_in_the_settings(router_client, creator):  # noqa: F811
    project_id = router_client.post("/api/v1/projects", json=_payload(creator.id)).json()["id"]

    resp = router_client.patch(f"/api/v1/projects/{project_id}", json={"private_network": True})

    assert resp.status_code == 200, resp.text
    assert resp.json()["private_network"] is True
    assert (
        router_client.patch(f"/api/v1/projects/{project_id}", json={"name": "Iné meno"}).json()["private_network"]
        is True
    )


def test_the_form_offers_the_option_switched_off(router_client):  # noqa: F811
    assert router_client.get("/api/v1/projects/create-presets").json()["private_network"] is False

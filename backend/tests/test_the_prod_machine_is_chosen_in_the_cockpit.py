"""DEV-274 — the machine a customer's PROD runs on is chosen in the cockpit, not written into its database.

31.10.2026 the Director decided ICC's production belongs on MAGER. Where PROD goes is ``customers.prod_host``; only
MÁGERSTAV had it, written straight into the database, and no screen or route could set it — the Manažér could neither
see nor change the machine. Now the customer form offers the machines the cockpit knows (one list, in the settings), a
machine it does not know is refused, and the PROD page names the machine at the customer's row.
"""

from __future__ import annotations

import uuid as _uuid

import pytest

from backend.api.routes.deploy import _adoption_context
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.schemas.customer import CustomerUpdate
from backend.services import customer as customer_service
from backend.services import prod_machines
from backend.tests.integration.test_workflow_deploy_matrix import _seed_customer


def _project(db) -> Project:
    owner = db.query(User).order_by(User.created_at.desc()).first()
    project = Project(
        name="ICC NEX Manager",
        slug=f"nex-manager-{_uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="DEV-274",
        created_by=owner.id,
    )
    db.add(project)
    db.flush()
    return project


def test_the_form_offers_this_machine_first_and_then_the_known_ones(client):
    answer = client.get("/api/v1/prod-machines")

    assert answer.status_code == 200
    assert answer.json() == [{"value": "", "label": "ANDROS"}, {"value": "mager", "label": "MAGER"}]


def test_icc_is_moved_to_mager_and_back(client, db_session):
    project = _project(db_session)
    icc = _seed_customer(db_session, project, "icc")
    assert client.get(f"/api/v1/projects/{project.slug}/customers").json()[0]["prod_machine"] == "ANDROS"

    moved = client.patch(f"/api/v1/customers/{icc.id}", json={"prod_host": "mager"})

    assert moved.status_code == 200
    assert (moved.json()["prod_host"], moved.json()["prod_machine"]) == ("mager", "MAGER")
    db_session.refresh(icc)
    assert icc.prod_host == "mager"
    # where a PROD deploy now goes — the very value the deploy and the adoption read
    assert _adoption_context(icc, project, "prod")[2] == "mager"
    assert _adoption_context(icc, project, "uat")[2] is None, "UAT stays on this machine"

    back = client.patch(f"/api/v1/customers/{icc.id}", json={"prod_host": ""})

    assert (back.json()["prod_host"], back.json()["prod_machine"]) == ("", "ANDROS")
    db_session.refresh(icc)
    assert icc.prod_host is None


def test_an_edit_that_does_not_name_the_machine_keeps_it(client, db_session):
    project = _project(db_session)
    icc = _seed_customer(db_session, project, "icc")
    client.patch(f"/api/v1/customers/{icc.id}", json={"prod_host": "mager"})

    renamed = client.patch(f"/api/v1/customers/{icc.id}", json={"notes": "presun na MAGER"})

    assert renamed.json()["prod_host"] == "mager"


def test_a_machine_the_cockpit_does_not_know_is_refused_and_nothing_changes(client, db_session):
    project = _project(db_session)
    icc = _seed_customer(db_session, project, "icc")

    # the service refuses before it touches the row (the route then rolls the whole request back)
    with pytest.raises(ValueError):
        customer_service.update(db_session, icc.id, CustomerUpdate(prod_host="mager2", notes="x"))
    assert icc.prod_host is None and icc.notes is None

    refused = client.patch(f"/api/v1/customers/{icc.id}", json={"prod_host": "mager2", "notes": "x"})

    assert refused.status_code == 422
    assert refused.json()["detail"] == "Ostrý stroj „mager2“ kokpit nepozná — na výber je: ANDROS, MAGER."


def test_a_new_customer_may_be_placed_on_mager_at_once(client, db_session):
    project = _project(db_session)

    made = client.post(
        f"/api/v1/projects/{project.slug}/customers", json={"name": "ICC s.r.o.", "slug": "icc", "prod_host": "mager"}
    )

    assert made.status_code == 201 and made.json()["prod_machine"] == "MAGER"


def test_the_prod_page_names_the_machine_at_the_row(client, db_session):
    project = _project(db_session)
    icc = _seed_customer(db_session, project, "icc")
    client.patch(f"/api/v1/customers/{icc.id}", json={"prod_host": "mager"})

    row = client.get(f"/api/v1/projects/{project.slug}/deploy-matrix").json()["rows"][0]

    assert row["prod_machine"] == "MAGER"


def test_a_machine_written_before_the_list_is_shown_by_its_name():
    assert prod_machines.label("stary-server") == "stary-server"
    assert prod_machines.label(None) == prod_machines.label("") == "ANDROS"

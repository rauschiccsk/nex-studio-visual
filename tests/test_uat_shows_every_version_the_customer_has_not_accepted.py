"""DEV-247 — the UAT guide of a customer covers every version its UAT carries that the customer has not accepted.

28.10.2026, NEX Manager for MÁGERSTAV: PROD ran 1.2.2 (accepted 24.09.2026), UAT got 1.4.1 — a fast fix carrying 1.3.0
and 1.4.0 (groups, a UAT walkthrough of about 40 steps) that MÁGERSTAV never accepted. The guide showed the one
version on UAT: „Nie je čo skúšať: na obrazovkách NEX Managera sa nič nemení“; an acceptance on that would have sent
groups to PROD without one try. Director: „Áno, súhlasím, založ tiket a oprav to“.

Pinned: the versions to try run from the newest the customer accepted or runs on PROD (exclusive) to the one on UAT
(inclusive), oldest first, finished builds only; a customer with nothing accepted and nothing on PROD tries the
version on UAT alone (a new customer gets the app whole — twenty old versions' walkthroughs would help nobody).
"""

from __future__ import annotations

from backend.db.models.deploy import DeployEvent
from backend.services import deploy as deploy_service
from tests.test_deploy_service import _make_customer, _make_project, _make_user
from tests.test_orchestrator_v2_vizual import _seed_state


def _world(db_session, finished=("1.2.2", "1.3.0", "1.4.0", "1.4.1"), unfinished=()):
    from backend.db.models.versions import Version

    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    customer = _make_customer(db_session, project, slug="mager", subdomain="mager")
    for number in (*finished, *unfinished):
        version = Version(project_id=project.id, version_number=number, name="dev")
        db_session.add(version)
        db_session.flush()
        _seed_state(db_session, version.id, stage="done" if number in finished else "programovanie", actor="ai_agent")
    db_session.flush()
    return project, customer, user


def _event(db_session, project, customer, user, version, environment, event_type="deploy", status="ok"):
    db_session.add(
        DeployEvent(
            customer_id=customer.id,
            project_id=project.id,
            version_number=version,
            environment=environment,
            event_type=event_type,
            status=status,
            actor_id=user.id,
            detail="",
        )
    )
    db_session.flush()


def _to_test(db_session, project, customer):
    return deploy_service.uat_versions_to_test(db_session, project, customer.id)


def test_the_nex_manager_case_shows_the_versions_mager_never_accepted(db_session) -> None:
    project, customer, user = _world(db_session)
    _event(db_session, project, customer, user, "1.2.2", "uat")
    _event(db_session, project, customer, user, "1.2.2", "uat", event_type="accept")
    _event(db_session, project, customer, user, "1.2.2", "prod")
    _event(db_session, project, customer, user, "1.4.1", "uat")

    assert _to_test(db_session, project, customer) == ["1.3.0", "1.4.0", "1.4.1"]


def test_the_newest_of_accepted_and_prod_is_where_the_trying_starts(db_session) -> None:
    project, customer, user = _world(db_session)
    _event(db_session, project, customer, user, "1.2.2", "prod")
    _event(db_session, project, customer, user, "1.3.0", "uat", event_type="accept")
    _event(db_session, project, customer, user, "1.4.1", "uat")

    assert _to_test(db_session, project, customer) == ["1.4.0", "1.4.1"]


def test_a_version_on_prod_counts_even_without_an_acceptance_on_record(db_session) -> None:
    """Customers deployed before the acceptance gate existed run PROD versions nobody recorded as accepted."""
    project, customer, user = _world(db_session)
    _event(db_session, project, customer, user, "1.4.0", "prod")
    _event(db_session, project, customer, user, "1.4.1", "uat")

    assert _to_test(db_session, project, customer) == ["1.4.1"]


def test_a_customer_with_nothing_accepted_and_nothing_on_prod_tries_the_version_on_uat(db_session) -> None:
    project, customer, user = _world(db_session)
    _event(db_session, project, customer, user, "1.4.1", "uat")

    assert _to_test(db_session, project, customer) == ["1.4.1"]


def test_a_build_that_never_finished_is_not_a_version_to_try(db_session) -> None:
    project, customer, user = _world(db_session, unfinished=("1.3.1",))
    _event(db_session, project, customer, user, "1.2.2", "prod")
    _event(db_session, project, customer, user, "1.4.1", "uat")

    assert _to_test(db_session, project, customer) == ["1.3.0", "1.4.0", "1.4.1"]


def test_versions_are_ordered_by_number_not_by_text(db_session) -> None:
    project, customer, user = _world(db_session, finished=("1.2.2", "1.9.0", "1.10.0", "1.10.2"))
    _event(db_session, project, customer, user, "1.2.2", "prod")
    _event(db_session, project, customer, user, "1.10.2", "uat")

    assert _to_test(db_session, project, customer) == ["1.9.0", "1.10.0", "1.10.2"]


def test_an_older_version_put_back_on_uat_is_tried_alone(db_session) -> None:
    project, customer, user = _world(db_session)
    _event(db_session, project, customer, user, "1.4.0", "uat", event_type="accept")
    _event(db_session, project, customer, user, "1.3.0", "uat")

    assert _to_test(db_session, project, customer) == ["1.3.0"]


def test_no_uat_installation_has_nothing_to_try(db_session) -> None:
    project, customer, _user = _world(db_session)

    assert _to_test(db_session, project, customer) == []


def test_the_matrix_row_carries_them(db_session) -> None:
    project, customer, user = _world(db_session)
    _event(db_session, project, customer, user, "1.2.2", "prod")
    _event(db_session, project, customer, user, "1.4.1", "uat")

    row = next(r for r in deploy_service.build_matrix(db_session, project)["rows"] if r["customer_id"] == customer.id)

    assert row["uat_versions_to_test"] == ["1.3.0", "1.4.0", "1.4.1"]

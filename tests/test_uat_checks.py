"""DEV-91 — what the manager tried in UAT is kept per customer and version, and the acceptance records it.

12.10.2026 the Director, with the UAT test guide (DEV-90): tick off each change he tried, see at the acceptance how
much of the version he tested, and turn a change that does not work into a fast fix. The changes are the sections of
the guide of the verified commit — or the release note's changes of an older version.
"""

from __future__ import annotations

from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent
from backend.services import uat_checks, uat_guide
from tests.test_delivered_tokens_statement import _as, _git, _write, project  # noqa: F401 — the fixture is used by name
from tests.test_uat_shows_what_to_try import GUIDE, _verified

DETAIL = "Detail faktúry v Chrome 109"
ISDOC = "Objednávkový kód z ISDOC"


def _world(project):  # noqa: F811
    db, root, owner, v010, _v011, _d010, _d011 = project
    _write(root, uat_guide.relative_path("0.2.0"), GUIDE)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "feat: 0.2.0")
    version = _verified(db, v010.project_id, "0.2.0", _git(root, "rev-parse", "HEAD").strip())
    customer = Customer(project_id=v010.project_id, name="MÁGERSTAV", slug="magerstav")
    other = Customer(project_id=v010.project_id, name="ICC s.r.o.", slug="icc")
    db.add_all([customer, other])
    db.flush()
    _as(db, owner)
    return db, owner, version, customer, other


def _put(client, customer, item, status, note=None):
    return client.put(
        f"/api/v1/customers/{customer.id}/uat-checks",
        json={"version_number": "0.2.0", "item": item, "status": status, "note": note},
    )


def _get(client, customer, version="0.2.0"):
    response = client.get(f"/api/v1/customers/{customer.id}/uat-checks", params={"version": version})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_changes_to_try_are_the_guides_sections_and_none_is_tried_at_first(client, project):  # noqa: F811
    _db, _owner, _version, customer, _other = _world(project)

    found = _get(client, customer)

    assert (found["items"], found["tested"], found["total"], found["untested"]) == (
        [DETAIL, ISDOC],
        0,
        2,
        [DETAIL, ISDOC],
    )


def test_what_was_tried_and_what_does_not_work_is_kept_per_customer(client, project):  # noqa: F811
    _db, owner, _version, customer, other = _world(project)

    assert _put(client, customer, DETAIL, "tested").status_code == 200
    found = _put(client, customer, ISDOC, "broken", "  Kód chýba pri 3 položkách.  ").json()

    assert (found["tested"], found["untested"], found["broken"]) == (1, [], [ISDOC])
    broken = next(c for c in found["checks"] if c["item"] == ISDOC)
    assert (broken["status"], broken["note"], broken["checked_by"]) == (
        "broken",
        "Kód chýba pri 3 položkách.",
        owner.username,
    )
    assert _get(client, customer) == found, "what is kept is on the server, not in the browser"
    assert _get(client, other)["tested"] == 0, "another customer's UAT is its own"


def test_a_record_can_be_taken_back_and_an_unknown_change_is_refused(client, project):  # noqa: F811
    _db, _owner, _version, customer, _other = _world(project)
    _put(client, customer, DETAIL, "tested")

    assert _put(client, customer, DETAIL, None).json()["tested"] == 0
    refused = _put(client, customer, "Zmena, ktorú návod nemá", "tested")
    assert (refused.status_code, refused.json()["detail"]) == (422, uat_checks.UNKNOWN_ITEM)


def test_the_acceptance_records_what_was_tested(client, project):  # noqa: F811
    db, _owner, _version, customer, _other = _world(project)
    db.add(
        DeployEvent(
            customer_id=customer.id,
            project_id=customer.project_id,
            version_number="0.2.0",
            environment="uat",
            event_type="deploy",
            status="ok",
        )
    )
    db.flush()
    _put(client, customer, ISDOC, "broken", "Kód chýba.")

    accepted = client.post(f"/api/v1/customers/{customer.id}/accept", json={"version_number": "0.2.0"})

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["detail"] == (
        f"UAT akceptované — PROD otvorené. Otestované 0 z 2. Neotestované: {DETAIL}. Nefunguje: {ISDOC}."
    )


def test_an_older_version_is_tried_by_the_changes_of_its_release_note(client, project):  # noqa: F811
    db, root, owner, v010, _v011, _d010, _d011 = project
    _write(
        root, "docs/specs/versions/v0.3.0/RELEASE_NOTES.md", "## v0.3.0\n\n- Export do CSV.\n- Rýchlejšie hľadanie.\n"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "docs: 0.3.0")
    _verified(db, v010.project_id, "0.3.0", _git(root, "rev-parse", "HEAD").strip())
    customer = Customer(project_id=v010.project_id, name="MÁGERSTAV", slug="magerstav")
    db.add(customer)
    db.flush()
    _as(db, owner)

    assert _get(client, customer, "0.3.0")["items"] == ["Export do CSV.", "Rýchlejšie hľadanie."]


def test_the_acceptance_sentence_says_everything_was_tested_without_lists():
    found = uat_checks.Progress(items=["A"], checks={"A": uat_checks.Check("A", "tested", None, None, None)})

    assert uat_checks.acceptance_detail(found) == "UAT akceptované — PROD otvorené. Otestované 1 z 1."
    assert uat_checks.acceptance_detail(uat_checks.Progress()) == (
        "UAT akceptované — PROD otvorené. Verzia nemá zoznam zmien na skúšku."
    )

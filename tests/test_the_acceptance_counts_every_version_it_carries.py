"""DEV-276 — the acceptance records what was tried in every version the UAT carries, as the question before it counts.

31.10.2026 NEX Manager 1.4.2 for MÁGERSTAV: the UAT carried the never-accepted 1.3.0, 1.4.0 and 1.4.1 too, the guide
and the question before „Akceptovať“ counted the checks of all four (DEV-247) — and the acceptance kept „Otestované 0
z 1“, the checks of 1.4.2 alone. The record now counts the same versions, each change named with its version.
"""

from __future__ import annotations

from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent
from backend.services import uat_checks, uat_guide
from tests.test_delivered_tokens_statement import _as, _git, _write, project  # noqa: F401 — the fixture is used by name
from tests.test_uat_shows_what_to_try import GUIDE, _verified

DETAIL = "Detail faktúry v Chrome 109"
ISDOC = "Objednávkový kód z ISDOC"


def _event(db, customer, version, event_type):
    db.add(
        DeployEvent(
            customer_id=customer.id,
            project_id=customer.project_id,
            version_number=version,
            environment="uat",
            event_type=event_type,
            status="ok",
        )
    )
    db.flush()


def _world(project):  # noqa: F811
    """MÁGERSTAV accepted 0.1.1; its UAT now runs 0.3.0, which carries the never-accepted 0.2.0."""
    db, root, owner, v010, _v011, _d010, _d011 = project
    _write(root, uat_guide.relative_path("0.2.0"), GUIDE)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "feat: 0.2.0")
    _verified(db, v010.project_id, "0.2.0", _git(root, "rev-parse", "HEAD").strip())
    _write(
        root, "docs/specs/versions/v0.3.0/RELEASE_NOTES.md", "## v0.3.0\n\n- Export do CSV.\n- Rýchlejšie hľadanie.\n"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "docs: 0.3.0")
    _verified(db, v010.project_id, "0.3.0", _git(root, "rev-parse", "HEAD").strip())
    customer = Customer(project_id=v010.project_id, name="MÁGERSTAV", slug="magerstav")
    db.add(customer)
    db.flush()
    _event(db, customer, "0.1.1", "accept")
    _event(db, customer, "0.3.0", "deploy")
    _as(db, owner)
    return db, customer


def _put(client, customer, version, item, status):
    answer = client.put(
        f"/api/v1/customers/{customer.id}/uat-checks",
        json={"version_number": version, "item": item, "status": status, "note": None},
    )
    assert answer.status_code == 200, answer.text


def test_the_acceptance_counts_the_checks_of_every_version_the_guide_asked_to_try(client, project):  # noqa: F811
    _db, customer = _world(project)
    _put(client, customer, "0.2.0", DETAIL, "tested")
    _put(client, customer, "0.3.0", "Export do CSV.", "broken")

    accepted = client.post(f"/api/v1/customers/{customer.id}/accept", json={"version_number": "0.3.0"})

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["detail"] == (
        "UAT akceptované — PROD otvorené. Otestované 1 z 4. "
        f"Neotestované: 0.2.0: {ISDOC}; 0.3.0: Rýchlejšie hľadanie. Nefunguje: 0.3.0: Export do CSV."
    )


def test_a_single_version_reads_exactly_as_before():
    found = uat_checks.Progress(items=[DETAIL, ISDOC])

    assert uat_checks.combine([("0.2.0", found)]) is found
    assert uat_checks.acceptance_detail(uat_checks.combine([("0.2.0", found)])) == (
        f"UAT akceptované — PROD otvorené. Otestované 0 z 2. Neotestované: {DETAIL}; {ISDOC}."
    )

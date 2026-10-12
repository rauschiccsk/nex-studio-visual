"""DEV-73 — the billing tab shows every version of a project for invoicing.

12.10.2026 the manager opened the costs page of NEX Inbox: the statement of the selected version, 1.7.1 — a fix of
our own error, 0 € — was all there was to see, and the versions between 1.5.0 and 1.7.1 looked lost. Nothing was:
1.7.0 had a valid statement, 1.5.0 and 1.6.0 had never had one issued, 1.5.1 to 1.5.6 were fast fixes waiting for their
work kind. The tab lists them all, the newest first, with where each stands and the totals.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import update

from backend.db.models.pipeline import PipelineState
from backend.db.models.versions import Version
from backend.services import delivery_statement as st
from tests.test_delivered_tokens_statement import _as, _rates, project  # noqa: F401 — the fixture is used by name


def _billing(client, db, v010):
    slug = db.get(Version, v010.id).project.slug
    response = client.get(f"/api/v1/projects/{slug}/billing")
    assert response.status_code == 200, response.text
    return response.json()


def test_every_version_is_listed_the_newest_first_with_where_it_stands(client, project):  # noqa: F811
    db, _root, owner, v010, v011, delivered_010, delivered_011 = project
    _rates(db, owner, 2.0, 1.0)
    _as(db, owner)

    billing = _billing(client, db, v010)

    assert [(r["version_number"], r["state"]) for r in billing["rows"]] == [("0.1.1", "needs_kind"), ("0.1.0", "ready")]
    fix, change = billing["rows"]
    assert fix["reason"] == st.KIND_UNDECIDED and fix["preview_amount_eur"] is None
    assert (change["base_sha"] is not None, change["delivered_sha"]) == (True, delivered_010)
    assert Decimal(change["preview_amount_eur"]) > 0 and change["issued"] is None
    assert Decimal(billing["ready_total_eur"]) == Decimal(change["preview_amount_eur"])
    assert (Decimal(billing["issued_total_eur"]), billing["needs_kind"]) == (Decimal("0"), 1)


def test_an_issued_statement_counts_as_issued_and_says_when_today_it_would_differ(client, project):  # noqa: F811
    db, _root, owner, v010, v011, _d010, _d011 = project
    _rates(db, owner, 2.0, 1.0)
    _as(db, owner)
    assert client.post(f"/api/v1/versions/{v010.id}/delivery-statement").status_code == 201
    assert client.put(f"/api/v1/versions/{v011.id}/work-kind", json={"work_kind": "fix"}).status_code == 200

    billing = _billing(client, db, v010)
    fix, change = billing["rows"]
    assert (fix["state"], Decimal(fix["preview_amount_eur"])) == ("ready", Decimal("0"))
    assert change["state"] == "issued" and change["differs"] is False
    assert Decimal(billing["issued_total_eur"]) == Decimal(change["issued"]["amount_eur"])
    assert Decimal(billing["ready_total_eur"]) == Decimal("0"), "an issued version is not counted again as to issue"
    assert billing["needs_kind"] == 0

    _rates(db, owner, 4.0, 1.0)  # the rate changed after the statement was issued
    changed = _billing(client, db, v010)["rows"][1]
    assert changed["state"] == "issued" and changed["differs"] is True
    assert changed["issued"]["amount_eur"] == change["issued"]["amount_eur"], "an issued statement never changes"
    assert Decimal(changed["preview_amount_eur"]) > Decimal(changed["issued"]["amount_eur"])


def test_a_version_still_in_build_is_listed_but_not_counted(client, project):  # noqa: F811
    db, _root, owner, v010, v011, _d010, _d011 = project
    _rates(db, owner, 2.0, 1.0)
    db.execute(update(PipelineState).where(PipelineState.version_id == v011.id).values(current_stage="programovanie"))
    db.flush()
    _as(db, owner)

    billing = _billing(client, db, v010)

    assert billing["rows"][0]["state"] == "not_done" and billing["rows"][0]["reason"] == st.NOT_DONE
    assert billing["needs_kind"] == 0

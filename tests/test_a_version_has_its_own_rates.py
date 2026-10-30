"""DEV-74 — a version may have its own token rates for its delivery statement.

12.10.2026 the Director: the rates in Nastavenia stay the default, but for a particular version he wants to set other
ones. Only that version counts with them; an issued statement keeps the rates it was issued with, and every change is
kept — who, when, from what to what.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError

from backend.core.kilotokens import kilotokens
from backend.db.models.versions import Version
from backend.services import delivery_statement as st
from tests.test_delivered_tokens_statement import _as, _rates, project  # noqa: F401 — the fixture is used by name


def _view(client, version):
    response = client.get(f"/api/v1/versions/{version.id}/delivery-statement")
    assert response.status_code == 200, response.text
    return response.json()


def _set(client, version, code, docs):
    return client.put(f"/api/v1/versions/{version.id}/billing-rates", json={"rate_code": code, "rate_docs": docs})


def test_only_the_version_counts_with_its_own_rate(client, project):  # noqa: F811
    db, _root, owner, v010, v011, _d010, _d011 = project
    _rates(db, owner, 2.0, 1.0)
    _as(db, owner)
    before = _view(client, v010)["preview"]

    assert _set(client, v010, "4", None).status_code == 200

    own = _view(client, v010)["preview"]
    assert (Decimal(own["rate_code"]), Decimal(own["rate_docs"])) == (Decimal("4"), Decimal("1"))
    assert (own["rate_code_own"], own["rate_docs_own"]) == (True, False)
    assert (Decimal(own["settings_rate_code"]), Decimal(own["settings_rate_docs"])) == (Decimal("2"), Decimal("1"))
    code_tokens = own["code"]["tokens"] + own["tests"]["tokens"]
    # DEV-263: kT rounded up to a tenth × rate, the euros half up to a tenth.
    assert Decimal(own["amount_code_eur"]) == (kilotokens(code_tokens) * 4).quantize(Decimal("0.1"), ROUND_HALF_UP)
    assert own["amount_docs_eur"] == before["amount_docs_eur"]

    other = _view(client, v011)["preview"]
    assert (Decimal(other["rate_code"]), other["rate_code_own"]) == (Decimal("2"), False), "another version moved"


def test_an_issued_statement_keeps_its_rates_and_a_replacement_takes_the_new_ones(client, project):  # noqa: F811
    db, _root, owner, v010, _v011, _d010, _d011 = project
    _rates(db, owner, 2.0, 1.0)
    _as(db, owner)
    issued = client.post(f"/api/v1/versions/{v010.id}/delivery-statement").json()

    assert _set(client, v010, "4", "3").status_code == 200

    view = _view(client, v010)
    assert view["issued"][0]["amount_eur"] == issued["amount_eur"], "an issued statement never changes"
    assert Decimal(view["issued"][0]["rate_code"]) == Decimal("2")
    replaced = client.post(f"/api/v1/versions/{v010.id}/delivery-statement", json={"replace": issued["id"]}).json()
    assert (Decimal(replaced["rate_code"]), Decimal(replaced["rate_docs"])) == (Decimal("4"), Decimal("3"))
    assert Decimal(replaced["amount_eur"]) > Decimal(issued["amount_eur"])


def test_every_change_is_kept_and_empty_returns_to_nastavenia(client, project):  # noqa: F811
    db, _root, owner, v010, _v011, _d010, _d011 = project
    _rates(db, owner, 2.0, 1.0)
    _as(db, owner)

    _set(client, v010, "4", None)
    assert _set(client, v010, "4", None).json() == _set(client, v010, "4.0000", None).json(), "no change, no record"
    changes = _set(client, v010, None, None).json()

    assert [(c["previous_rate_code"], c["rate_code"]) for c in changes] == [("4.0000", None), (None, "4.0000")]
    assert changes[0]["changed_by"] == owner.username
    back = _view(client, v010)["preview"]
    assert (Decimal(back["rate_code"]), back["rate_code_own"]) == (Decimal("2"), False)
    assert [c["rate_code"] for c in _view(client, v010)["rate_changes"]] == [None, "4.0000"]


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_a_rate_that_is_no_price_is_refused(client, project, bad):  # noqa: F811
    db, _root, owner, v010, _v011, _d010, _d011 = project
    _as(db, owner)

    refused = _set(client, v010, bad, None)

    assert refused.status_code == 422 and refused.json()["detail"] == st.RATE_NOT_POSITIVE
    assert _view(client, v010)["rate_changes"] == []


def test_the_database_refuses_a_zero_rate_too(project):  # noqa: F811
    db, _root, _owner, v010, _v011, _d010, _d011 = project
    with pytest.raises(DBAPIError):  # pg8000 reports a check violation as a ProgrammingError
        db.execute(update(Version).where(Version.id == v010.id).values(rate_code=0))
        db.flush()
    db.rollback()


def test_the_billing_tab_says_which_versions_have_their_own_rate(client, project):  # noqa: F811
    db, _root, owner, v010, _v011, _d010, _d011 = project
    _rates(db, owner, 2.0, 1.0)
    _as(db, owner)
    _set(client, v010, None, "1.5")

    rows = {
        r["version_number"]: r
        for r in client.get(f"/api/v1/projects/{db.get(Version, v010.id).project.slug}/billing").json()["rows"]
    }

    assert (rows["0.1.0"]["own_rates"], Decimal(rows["0.1.0"]["rate_docs"])) == (True, Decimal("1.5"))
    assert rows["0.1.1"]["own_rates"] is False

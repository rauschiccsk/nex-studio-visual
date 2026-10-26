"""DEV-225 — the orientačná cena belongs to the billing tab: every version with a plan carries it there.

26.10.2026: the estimate was measured only when somebody opened the version's page (its panel), and the billing tab
showed what that left behind — Author Asistent 0.1.0 got it at 09:56, when the Director opened the page; seven versions
with a plan since 15.10 had none on the billing tab. Director: „podľa mňa táto informácia vecne patrí skôr do priečinku
Fakturácia“ … „Áno, súhlasím, založ tiket a implementuj“.

Pinned: the billing tab measures the estimate of a version whose page nobody opened, with what it rests on; the
measurements are refreshed once for the whole tab; a fix of our error and a version without a plan have none; someone
who may not see the project gets nothing and nothing is kept; the version's page has no estimate of its own any more.
"""

from __future__ import annotations

from decimal import Decimal

from backend.core.security import get_current_user
from backend.main import app
from backend.services import delivery_statement, price_estimate
from tests.test_a_version_gets_its_orientacna_cena_after_navrh import _owner, _version, measured  # noqa: F401


def _rows(monkeypatch, *versions) -> None:
    monkeypatch.setattr(
        delivery_statement,
        "billing_rows",
        lambda db, project, repo: [
            delivery_statement.BillingRow(
                version=v,
                flow_type="new_version",
                preview=delivery_statement.Preview(version_number=v.version_number),
                valid=None,
                state="not_done",
            )
            for v in versions
        ],
    )


def _billing(client, project, user) -> dict:
    app.dependency_overrides[get_current_user] = lambda: user
    return client.get(f"/api/v1/projects/{project.slug}/billing")


def test_a_version_whose_page_nobody_opened_has_its_estimate_on_the_billing_tab(client, measured, monkeypatch):  # noqa: F811
    db, owner, inbox = measured
    monkeypatch.setattr(price_estimate, "refresh_samples", lambda db, root: 0)
    version = _version(db, inbox, "1.4.0", done=False, tasks=10, day=4)
    _rows(monkeypatch, version)
    db.commit()

    estimate = _billing(client, inbox, owner).json()["rows"][0]["estimate"]

    assert {k: estimate[k] for k in ("amount_eur", "spread_pct", "code_eur", "docs_eur")} == {
        "amount_eur": "111.67",
        "spread_pct": 12,
        "code_eur": "100.00",
        "docs_eur": "11.67",
    }
    basis = (estimate["tasks"], estimate["samples"], estimate["enough"], estimate["first_version"])
    assert basis == (10, 3, False, False)
    assert price_estimate.stored(db, version.id).amount_eur == Decimal("111.67")  # kept, as the tab showed it


def test_the_measurements_are_refreshed_once_for_the_whole_tab(client, measured, monkeypatch):  # noqa: F811
    db, owner, inbox = measured
    refreshed: list[int] = []
    monkeypatch.setattr(price_estimate, "refresh_samples", lambda db, root: refreshed.append(1) or 0)
    _rows(
        monkeypatch,
        _version(db, inbox, "1.4.0", done=False, tasks=10, day=4),
        _version(db, inbox, "1.5.0", done=False, tasks=4, day=5),
    )
    db.commit()

    rows = _billing(client, inbox, owner).json()["rows"]

    assert [r["estimate"]["tasks"] for r in rows] == [10, 4]
    assert refreshed == [1]


def test_a_fix_of_our_error_and_a_version_without_a_plan_have_none_on_the_tab(client, measured, monkeypatch):  # noqa: F811
    db, owner, inbox = measured
    monkeypatch.setattr(price_estimate, "refresh_samples", lambda db, root: 0)
    _rows(
        monkeypatch,
        _version(db, inbox, "1.3.1", done=False, tasks=3, day=4, flow="fast_fix", work_kind="fix"),
        _version(db, inbox, "1.5.0", done=False, tasks=0, day=5),
    )
    db.commit()

    rows = _billing(client, inbox, owner).json()["rows"]

    assert [(r["version_number"], r["estimate"]) for r in rows] == [("1.3.1", None), ("1.5.0", None)]


def test_someone_who_may_not_see_the_project_gets_no_billing_and_nothing_is_kept(client, measured, monkeypatch):  # noqa: F811
    db, _owner_, inbox = measured
    monkeypatch.setattr(price_estimate, "refresh_samples", lambda db, root: 0)
    version = _version(db, inbox, "1.4.0", done=False, tasks=10, day=4)
    _rows(monkeypatch, version)
    stranger = _owner(db)
    db.commit()

    response = _billing(client, inbox, stranger)

    assert response.status_code in (403, 404)
    assert price_estimate.stored(db, version.id) is None


def test_the_version_page_has_no_estimate_of_its_own(client, measured, monkeypatch):  # noqa: F811
    db, owner, inbox = measured
    monkeypatch.setattr(price_estimate, "refresh_samples", lambda db, root: 0)
    version = _version(db, inbox, "1.4.0", done=False, tasks=10, day=4)
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: owner

    response = client.get(f"/api/v1/versions/{version.id}/price-estimate")

    assert response.status_code == 404
    assert price_estimate.stored(db, version.id) is None

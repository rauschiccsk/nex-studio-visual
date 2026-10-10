"""DEV-54 — one valid delivered-token statement per version: a new issue replaces the previous one.

Director 10.10.2026, after issuing NEX Inbox 1.7.0 twice (423,87 € and 249,98 €) with nothing saying which one is
valid: a new statement of the same version replaces the previous one; the old one stays in the list, marked as
replaced — only one is valid, and an invoice is made from that one („Áno, založ tiket do DEV“ — „a hneď
implementuj“). Replacing needs the Manažér's confirmation of exactly the statement that is valid.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from backend.db.models.delivery_statement import DeliveryStatement
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import delivery_statement as st
from tests.test_delivered_tokens_statement import _as, _issued, _rates, project  # noqa: F401 — the fixture

ISSUED_AT = datetime(2026, 10, 10, 13, 36, 8, tzinfo=timezone.utc)
REPLACED_AT = datetime(2026, 10, 10, 13, 47, 36, tzinfo=timezone.utc)


def _count(db, version) -> int:
    return db.execute(
        select(func.count()).select_from(DeliveryStatement).where(DeliveryStatement.version_id == version.id)
    ).scalar_one()


def test_issuing_again_needs_the_confirmation_of_the_valid_statement(project):  # noqa: F811
    db, root, owner, v010, _v011, _, _ = project
    _rates(db, owner, 4.0, 1.5)
    first = st.issue(db, v010, root, owner.id)
    assert (first.replaces_id, first.replaced_at) == (None, None), "prvý súpis verzie platí"

    with pytest.raises(st.CannotIssue) as refused:
        st.issue(db, v010, root, owner.id)

    message = str(refused.value)
    assert message.startswith("Verzia už má platný súpis z ")
    assert message.endswith(f" za {str(first.amount_eur).replace('.', ',')} € — nový ho nahradí, len keď to potvrdíš.")
    assert _count(db, v010) == 1, "nepotvrdené vydanie aj tak zapísalo súpis"


def test_a_confirmed_statement_replaces_the_valid_one_which_stays_marked(project):  # noqa: F811
    db, root, owner, v010, _v011, _, _ = project
    _rates(db, owner, 4.0, 1.5)
    first = st.issue(db, v010, root, owner.id)
    _rates(db, owner, 2.0, 1.0)

    second = st.issue(db, v010, root, owner.id, replace=first.id)
    db.refresh(first)

    assert second.replaces_id == first.id and second.replaced_at is None
    assert first.replaced_at is not None and first.replaced_at == second.created_at
    assert first.amount_eur != second.amount_eur, "nahradený súpis si drží svoje čísla"
    assert st.valid_statement(db, v010.id).id == second.id
    assert _count(db, v010) == 2, "nahradený súpis sa nezmazal"


def test_a_stale_confirmation_replaces_nothing(project):  # noqa: F811
    db, root, owner, v010, v011, _, _ = project
    _rates(db, owner, 4.0, 1.5)
    first = st.issue(db, v010, root, owner.id)
    st.issue(db, v010, root, owner.id, replace=first.id)

    with pytest.raises(st.CannotIssue, match="len keď to potvrdíš"):
        st.issue(db, v010, root, owner.id, replace=first.id)
    assert _count(db, v010) == 2

    v011.work_kind = "change"
    db.flush()
    with pytest.raises(st.CannotIssue) as refused:
        st.issue(db, v011, root, owner.id, replace=uuid.uuid4())
    assert str(refused.value) == st.VALID_CHANGED
    assert _count(db, v011) == 0


def test_the_database_keeps_one_valid_statement_per_version(project):  # noqa: F811
    # pg8000 reports a violated unique index as ProgrammingError, psycopg as IntegrityError — both are DBAPIError.
    from sqlalchemy.exc import DBAPIError

    db, _root, _owner, v010, _v011, _, _ = project
    _issued(db, v010)
    with pytest.raises(DBAPIError, match="ux_delivery_statements_one_valid"):
        with db.begin_nested():
            _issued(db, v010)


def test_the_csv_of_a_replaced_statement_says_it_must_not_be_invoiced(project):  # noqa: F811
    db, _root, _owner, v010, _v011, _, _ = project
    project_row = db.get(Project, v010.project_id)
    replaced = _issued(db, v010, replaced_at=REPLACED_AT)
    valid = _issued(db, v010, replaces_id=replaced.id, created_at=REPLACED_AT)

    replaced_lines = st.csv_text(replaced, v010, project_row).splitlines()
    valid_lines = st.csv_text(valid, v010, project_row).splitlines()

    assert replaced_lines[:3] == [
        "Súpis dodaných tokenov",
        "NAHRADENÝ súpisom z 10.10.2026 15:47:36 — nepoužiť na faktúru",
        "Projekt;Faktúry",
    ]
    assert valid_lines[:2] == ["Súpis dodaných tokenov", "Projekt;Faktúry"]


def test_the_screen_issues_a_replacement_only_when_confirmed(client, project):  # noqa: F811
    db, _root, owner, v010, _v011, _, _ = project
    _rates(db, owner, 4.0, 1.5)
    _as(db, owner)
    url = f"/api/v1/versions/{v010.id}/delivery-statement"

    first = client.post(url, json={})
    assert first.status_code == 201, first.text
    again = client.post(url, json={})
    assert again.status_code == 409 and again.json()["detail"].endswith("len keď to potvrdíš.")
    assert client.post(url).status_code == 409, "bez tela je to to isté ako bez potvrdenia"

    second = client.post(url, json={"replace": first.json()["id"]})
    assert second.status_code == 201, second.text

    issued = client.get(url).json()["issued"]
    assert [(s["id"], s["replaces_id"], s["replaced_at"] is None) for s in issued] == [
        (second.json()["id"], first.json()["id"], True),
        (first.json()["id"], None, False),
    ], "platný hore, nahradený pod ním"

    csv = client.get(f"/api/v1/delivery-statements/{first.json()['id']}/csv")
    assert csv.status_code == 200 and "-nahradeny.csv" in csv.headers["content-disposition"]
    assert csv.content.decode("utf-8-sig").splitlines()[1].startswith("NAHRADENÝ súpisom z ")


def test_two_issues_at_once_end_in_one_valid_statement_and_a_sentence(client, project, monkeypatch):  # noqa: F811
    """The second of two simultaneous issues does not see the first one's statement yet — the database refuses it
    and the Manažér gets the sentence, not an error page."""
    db, _root, owner, v010, _v011, _, _ = project
    _rates(db, owner, 4.0, 1.5)
    _issued(db, v010)
    _as(db, owner)
    monkeypatch.setattr(st, "valid_statement", lambda _db, _version_id: None)

    raced = client.post(f"/api/v1/versions/{v010.id}/delivery-statement", json={})

    assert (raced.status_code, raced.json()["detail"]) == (409, st.VALID_CHANGED)


# ── statements issued before the replacement was kept ───────────────────────


def test_statements_issued_before_are_read_as_each_issue_replacing_the_one_before(monkeypatch):
    """Migration 112: on each version the newest statement stays valid and each older one is replaced at the moment
    the next one was issued — NEX Inbox 1.7.0 keeps 249,98 € valid, 423,87 € replaced."""
    from alembic import command
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.orm import Session

    from tests.test_migration_versions import (
        _alembic_config,
        _create_clean_database,
        _drop_database_if_exists,
        _get_test_database_url,
    )

    base_url = _get_test_database_url()
    parts = base_url.rsplit("/", 1)
    admin_url = parts[0] + "/postgres"
    db_name = parts[1].split("?")[0] + "_mig112"
    db_url = parts[0] + "/" + db_name
    _create_clean_database(admin_url, db_name)
    engine = create_engine(db_url)
    try:
        config = _alembic_config(db_url, monkeypatch)
        command.upgrade(config, "111")
        with Session(engine) as db:
            owner = User(username="u112", email="u112@t.sk", password_hash="x", role="ri")
            db.add(owner)
            db.flush()
            proj = Project(
                name="P", slug="p112", type="standard", auth_mode="password", description="", created_by=owner.id
            )
            db.add(proj)
            db.flush()
            inbox = Version(project_id=proj.id, version_number="1.7.0", name="1.7.0", status="active")
            other = Version(project_id=proj.id, version_number="1.6.0", name="1.6.0", status="active")
            db.add_all([inbox, other])
            db.commit()
            inbox_id, other_id = inbox.id, other.id
        issued = [
            # (version, delivered_sha as a name, issued at, total)
            (inbox_id, "prvy", ISSUED_AT, "423.87"),
            (inbox_id, "druhy", REPLACED_AT, "249.98"),
            (inbox_id, "treti", datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc), "300.00"),
            (other_id, "jediny", ISSUED_AT, "100.00"),
        ]
        with engine.begin() as conn:
            for version_id, name, at, total in issued:
                conn.execute(
                    text(
                        "INSERT INTO delivery_statements (id, version_id, base_sha, delivered_sha, delivered_source, "
                        "tokenizer, work_kind, tokens_code, tokens_tests, tokens_docs, rate_code, rate_docs, "
                        "amount_eur, files, created_at) VALUES (gen_random_uuid(), :v, 'a', :name, 'verifikacia', "
                        "'o200k_base', 'change', 1, 1, 1, 1, 1, :total, '[]'::jsonb, :at)"
                    ),
                    {"v": version_id, "name": name, "total": Decimal(total), "at": at},
                )

        command.upgrade(config, "112")

        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT s.delivered_sha, r.delivered_sha AS replaces, s.replaced_at FROM delivery_statements s "
                    "LEFT JOIN delivery_statements r ON r.id = s.replaces_id ORDER BY s.created_at, s.delivered_sha"
                )
            ).all()
        assert [(r.delivered_sha, r.replaces, r.replaced_at) for r in rows] == [
            ("jediny", None, None),
            ("prvy", None, REPLACED_AT),
            ("druhy", "prvy", datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)),
            ("treti", "druhy", None),
        ]
        with pytest.raises(DBAPIError, match="ux_delivery_statements_one_valid"):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO delivery_statements (id, version_id, base_sha, delivered_sha, "
                        "delivered_source, tokenizer, work_kind, tokens_code, tokens_tests, tokens_docs, rate_code, "
                        "rate_docs, amount_eur, files) VALUES (gen_random_uuid(), :v, 'a', 'stvrty', 'verifikacia', "
                        "'o200k_base', 'change', 1, 1, 1, 1, 1, 1, '[]'::jsonb)"
                    ),
                    {"v": inbox_id},
                )

        command.downgrade(config, "111")
        with engine.connect() as conn:
            columns = {
                r[0]
                for r in conn.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = 'delivery_statements'")
                )
            }
        assert {"replaces_id", "replaced_at"} & columns == set()
    finally:
        engine.dispose()
        _drop_database_if_exists(admin_url, db_name)

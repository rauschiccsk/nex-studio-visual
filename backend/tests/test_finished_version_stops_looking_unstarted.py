"""Dokončená stavba prestáva vyzerať ako nezačatá (ICCINT-50).

01.09.2026, nex-productcatalogs v0.1.0: stavba prešla celým priebehom — Príprava, Návrh, Vizuál,
Programovanie, Verifikácia PASS, Manažér schválil na Hotovo. Priebeh ``done/done``, plán 147/147.
Verzia pritom v evidencii zostala ``planned`` a na obrazovke sa zobrazila ako „Plánované".

Hotová a nezačatá verzia vyzerali rovnako — a keďže detail verzie vetví obsah podľa stavu, nad
postavenou a schválenou verziou sa Manažérovi ponúkal panel s návodom, ako ju spustiť.

Príčinou bolo, že medzi ``planned`` a ``released`` neexistoval prechod, ktorý by zaznamenal, že sa
stavba stala: ``auto_activate`` sa volalo jedine pri ručnej úprave epiky, čo priebeh nikdy nerobí.
"""

from __future__ import annotations

import uuid as _uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import version as version_service


def _version(db, status: str = "planned") -> Version:
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"u_{suffix}", email=f"u_{suffix}@t.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"P {suffix}",
        slug=f"p-{suffix}",
        type="standard",
        auth_mode="password",
        description="ICCINT-50",
        created_by=user.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    v = Version(project_id=project.id, version_number="0.1.0", status=status)
    db.add(v)
    db.flush()
    return v


def test_a_finished_build_is_recorded_on_the_version(db_session) -> None:
    """Bez tohto zostane postavená a schválená verzia „naplánovaná" — nerozoznateľná od nezačatej."""
    v = _version(db_session, "active")
    version_service.mark_done(db_session, v.id)
    assert v.status == "done"


def test_a_build_that_was_never_activated_still_lands_on_done(db_session) -> None:
    """Presne prípad tých štyroch stavieb: priebeh ich nikdy neprepol na ``active``, lebo ten prechod
    sa spúšťal iba pri ručnej úprave epiky. Aj tak sú hotové."""
    v = _version(db_session, "planned")
    version_service.mark_done(db_session, v.id)
    assert v.status == "done"


def test_a_released_version_is_never_pulled_back_to_done(db_session) -> None:
    """Stavy idú len dopredu. Nasadená verzia je ĎALEJ než dokončená — opačný krok by z vydaného
    produktu spravil rozrobený."""
    v = _version(db_session, "released")
    version_service.mark_done(db_session, v.id)
    assert v.status == "released"


def test_done_is_permitted_by_the_database_constraint() -> None:
    """Hodnotu musí pustiť aj obmedzenie v databáze, nielen Python.

    Prvá verzia tohto testu zakladala riadok so stavom ``done`` a čakala, že to obmedzenie odmietne.
    Prešla však aj proti neopravenému kódu — schéma testovacej databázy vzniká raz na začiatku behu,
    takže zmena modelu sa do nej už nepremietla. Test, ktorý nedokáže spadnúť, dáva falošnú istotu,
    tak sa pozeráme priamo na deklarované obmedzenie.
    """
    from sqlalchemy import CheckConstraint

    from backend.db.models.versions import Version

    checks = [c for c in Version.__table__.constraints if isinstance(c, CheckConstraint)]
    status_check = next(c for c in checks if c.name == "ck_versions_status")
    assert "'done'" in str(status_check.sqltext)


def test_the_schema_admits_done() -> None:
    from typing import get_args

    from backend.schemas.version import VersionStatus

    assert "done" in get_args(VersionStatus)


@pytest.mark.parametrize("start", ["planned", "active"])
def test_the_pipeline_marks_the_version_done_at_signoff(start: str) -> None:
    """Podpis Hotovo musí siahnuť aj na verziu — inak sa dokončenie nikde nezaznamená."""
    import inspect

    from backend.services import orchestrator

    src = inspect.getsource(orchestrator._apply_hotovo_signoff)
    assert "version_service.mark_done" in src, "koniec stavby verziu nedotýka"
    del start

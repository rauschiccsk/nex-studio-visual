"""Rozloženie bloku portov sa naozaj vynucuje, nielen sľubuje v komentári (ICCINT-23).

V ``projects.py`` roky stála poznámka „Frontend / db must follow the D-020 layout (+0 BE, +1 FE, +2 DB)“,
zatiaľ čo kontrola bola len na porte backendu. Preto sa 23.08.2026 dal uložiť frontend 10193 vedľa backendu
10190 — pravidlo existovalo len ako veta v komentári.

⚠️ Pravidlo je odvtedy vynútené, ale **nemalo žiadnu stráž**. Pravidlo bez skúšky je poznámka v komentári
s lepším písmom: ticho zmizne pri prvej úprave a nikto sa to nedozvie, kým sa bloky nerozsypú.

Najdôležitejšie je tu tretie tvrdenie. Pôvodná chyba nebola, že sa kontrola nezavolala — bola v tom, že sa
zavolala len vtedy, keď požiadavka NÁHODOU niesla aj port backendu. Pravidlo, ktoré platí len keď pošleš
nesúvisiace pole, nie je pravidlo, ale zhoda okolností.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from backend.api.routes.projects import _validate_ports
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.schemas.project import ProjectUpdate

BASE = 10990  # začiatok voľného bloku — kontrola zarovnania ho prijme


def _seed(db_session, *, backend_port: int) -> Project:
    owner = User(
        id=uuid.uuid4(),
        username=f"blok-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.test",
        password_hash="x",  # noqa: S106 — nie je to tajomstvo, stĺpec je nenulový
        role="ri",
    )
    db_session.add(owner)
    db_session.flush()
    project = Project(
        created_by=owner.id,
        id=uuid.uuid4(),
        name="blok-test",
        slug=f"blok-test-{uuid.uuid4().hex[:8]}",
        type="web",
        auth_mode="password",
        description="projekt na overenie rozloženia portov",
        backend_port=backend_port,
        frontend_port=backend_port + 1,
        db_port=backend_port + 2,
    )
    db_session.add(project)
    db_session.flush()
    return project


def test_a_correct_block_passes(db_session) -> None:
    _validate_ports(db_session, ProjectUpdate(backend_port=BASE, frontend_port=BASE + 1, db_port=BASE + 2))


@pytest.mark.parametrize(
    ("field", "value", "expect"),
    [("frontend_port", BASE + 3, "Frontend"), ("db_port", BASE + 5, "Databáza")],
)
def test_a_port_outside_its_place_in_the_block_is_refused(db_session, field, value, expect) -> None:
    with pytest.raises(HTTPException) as refused:
        _validate_ports(db_session, ProjectUpdate(backend_port=BASE, **{field: value}))

    assert refused.value.status_code == 422
    detail = str(refused.value.detail)
    assert str(value) in detail and str(BASE) in detail, "hláška musí menovať oba porty, nech je čo opraviť"


def test_the_rule_holds_when_only_the_frontend_port_is_sent(db_session) -> None:
    """TOTO je pôvodný nález. Manažér menil iba frontend port — a keďže požiadavka nenies­la port backendu,
    preskočila sa celá kontrola vrátane rozloženia. Tak sa uložil frontend 10193 vedľa backendu 10190."""
    project = _seed(db_session, backend_port=BASE)

    with pytest.raises(HTTPException) as refused:
        _validate_ports(db_session, ProjectUpdate(frontend_port=BASE + 3), project_id=project.id)

    assert refused.value.status_code == 422
    assert str(BASE + 1) in str(refused.value.detail), "hláška musí povedať, ktorý port tam patrí"


def test_the_message_is_something_a_non_expert_can_act_on(db_session) -> None:
    """Od ICCINT-22 túto vetu číta Manažér priamo na obrazovke, nie vývojár v logu."""
    with pytest.raises(HTTPException) as refused:
        _validate_ports(db_session, ProjectUpdate(backend_port=BASE, frontend_port=BASE + 3))

    detail = str(refused.value.detail)
    assert "column" not in detail.lower() and "NULL" not in detail
    assert "patrí" in detail, "hláška má povedať, ktorý port tam patrí, nie iba že je zlý"

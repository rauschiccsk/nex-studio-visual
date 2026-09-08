"""Projekt sa dá zveriť inému pracovníkovi — a jedine admin (ICCINT-78).

Táto funkcia nie je pohodlie. Nahrádza zdieľané prihlasovacie údaje: Director s Tiborom si ich dovtedy
vymieňali, aby sa vedeli zastúpiť, a 08.09.2026 to zrušil — zdieľané heslo zmaže stopu, kto čo urobil,
a pravidlo „nič nezvratné bez Directora“ sa opiera práve o podpis konta (D-028). Bez presunu projektu
niet čím tú zastupiteľnosť nahradiť.
"""

from __future__ import annotations

import uuid

import pytest

from backend.core import authz
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import project as project_service


def _user(db_session, username: str, *, active: bool = True) -> User:
    user = User(
        id=uuid.uuid4(),
        username=username,
        email=f"{username}-{uuid.uuid4().hex[:6]}@example.test",
        password_hash="x",  # noqa: S106 — nenulový stĺpec, nie tajomstvo
        role="shu",
        is_active=active,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _project(db_session, owner: User) -> Project:
    project = Project(
        id=uuid.uuid4(),
        created_by=owner.id,
        name="presun-test",
        slug=f"presun-{uuid.uuid4().hex[:8]}",
        type="web",
        auth_mode="password",
        description="projekt na overenie presunu",
        backend_port=10800,
        frontend_port=10801,
        db_port=10802,
    )
    db_session.add(project)
    db_session.flush()
    return project


def test_the_project_moves_from_one_desk_to_another(db_session) -> None:
    """Jadro: po presune je vlastníkom nový človek — a to je to isté pole, na ktorom stojí aj to,
    čí projekt sa komu zobrazí."""
    spravca = _user(db_session, "spravca")
    tibor = _user(db_session, "tibor")
    nazar = _user(db_session, "nazar-p")
    projekt = _project(db_session, nazar)

    project_service.reassign(db_session, projekt.id, to_user_id=tibor.id, assigned_by=spravca.id)

    assert projekt.created_by == tibor.id
    assert authz.is_owner_or_admin(tibor, projekt.created_by) is True
    assert authz.is_owner_or_admin(nazar, projekt.created_by) is False, (
        "z plochy predošlého vlastníka má projekt zmiznúť"
    )


def test_it_is_visible_who_had_it_before(db_session) -> None:
    """Presun slúži aj na zastupovanie počas neprítomnosti. Bez záznamu sa po pol roku nedá povedať,
    či bol natrvalo, alebo len na týždeň."""
    spravca = _user(db_session, "spravca2")
    tibor = _user(db_session, "tibor2")
    nazar = _user(db_session, "nazar2")
    projekt = _project(db_session, nazar)

    project_service.reassign(
        db_session, projekt.id, to_user_id=tibor.id, assigned_by=spravca.id, note="Nazar je do piatku preč"
    )

    history = project_service.assignment_history(db_session, projekt.id)
    assert len(history) == 1
    assert history[0].from_user_id == nazar.id
    assert history[0].to_user_id == tibor.id
    assert history[0].assigned_by == spravca.id, "musí byť vidieť, KTO presun urobil"
    assert history[0].note == "Nazar je do piatku preč"


def test_handing_it_to_the_same_person_changes_nothing(db_session) -> None:
    """Nečinnosť, nie chyba. Stráž, ktorá otravuje pri neškodnom kroku, sa naučí obchádzať — a história
    by sa plnila riadkami, v ktorých sa nič nestalo."""
    spravca = _user(db_session, "spravca3")
    nazar = _user(db_session, "nazar3")
    projekt = _project(db_session, nazar)

    project_service.reassign(db_session, projekt.id, to_user_id=nazar.id, assigned_by=spravca.id)

    assert project_service.assignment_history(db_session, projekt.id) == []


def test_a_deactivated_person_cannot_be_given_a_project(db_session) -> None:
    """Projekt bez činného gazdu je projekt, ktorý nikto nevidí a za ktorý nikto nezodpovedá."""
    spravca = _user(db_session, "spravca4")
    odisiel = _user(db_session, "odisiel", active=False)
    projekt = _project(db_session, _user(db_session, "nazar4"))

    with pytest.raises(ValueError, match="not active"):
        project_service.reassign(db_session, projekt.id, to_user_id=odisiel.id, assigned_by=spravca.id)


def test_only_the_admin_may_move_a_project() -> None:
    """⚠️ Najdôležitejšia stráž tejto úlohy — a zámerne na TRASE, nie v službe.

    Keby kontrolu robila služba, dalo by sa ju obísť ďalším volajúcim. Keby ju nerobil nikto, mal by
    presun v rukách každý, kto projekt vidí — a tým by sa z „jedine admin“ stala formulácia v dokumentácii.
    """
    import inspect

    from backend.api.routes import projects as routes

    src = inspect.getsource(routes.reassign_project)
    assert "authz.is_admin(current_user)" in src, "presun nekontroluje, či volá admin"
    assert "HTTP_403_FORBIDDEN" in src


def test_the_endpoints_are_mounted_where_the_cockpit_calls_them() -> None:
    """⚠️ Toto chýbalo a stálo to jedno nasadenie navyše.

    Trasy boli napísané ako ``/projects/{id}/reassign``, lenže router UŽ MÁ predponu ``/projects`` —
    v appke tak vznikla cesta ``/api/v1/projects/projects/{id}/reassign``. Kokpit volal správnu adresu
    a dostával 404 s textom „Not Found“ od FastAPI, teda ani nie našu hlášku.

    Prečo to nechytila žiadna z piatich stráží: skúšali funkciu a jej zdrojový text, nie **pripojenú
    adresu**. Ani brána zhody kontraktu to nechytí — generovaný klient sa robí z tej istej (zlej) mapy
    trás, takže si obe strany zle rozumejú zhodne.
    """
    from backend.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/v1/projects/{project_id}/reassign" in paths
    assert "/api/v1/projects/{project_id}/assignments" in paths

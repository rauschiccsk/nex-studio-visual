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


def _user(
    db_session,
    username: str,
    *,
    active: bool = True,
    telegram: str | None = None,
    first: str | None = None,
    last: str | None = None,
) -> User:
    user = User(
        id=uuid.uuid4(),
        username=username,
        email=f"{username}-{uuid.uuid4().hex[:6]}@example.test",
        password_hash="x",  # noqa: S106 — nenulový stĺpec, nie tajomstvo
        role="shu",
        is_active=active,
        telegram_chat_id=telegram,
        first_name=first,
        last_name=last,
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


# ─── Upozornenia idú s manažérom (ICCINT-80) ──────────────────────────────────
#
# Zodpovednosť a upozornenia sú v systéme DVA rôzne údaje: ``created_by`` (za projekt zodpovedá)
# a ``owner_id`` (chodia mu hlásenia z Telegramu). Prvá verzia zverenia presunula len ten prvý —
# zmerané naživo 08.09.2026 hneď po tom, čo Director zveril nex-shopify Tiborovi: zodpovedný bol
# `tibi`, adresát upozornení zostal `admin`. Tibor by o svojom vlastnom projekte nevedel.


def test_notifications_follow_the_new_manager(db_session) -> None:
    """Kto za projekt zodpovedá, tomu majú chodiť aj hlásenia. Inak zverenie presunie prácu, ale nie
    to, čím sa človek o práci dozvie."""
    spravca = _user(db_session, "spravca5")
    tibor = _user(db_session, "tibor5", telegram="111")
    nazar = _user(db_session, "nazar5", telegram="222")
    projekt = _project(db_session, nazar)
    projekt.owner_id = nazar.id
    db_session.flush()

    vysledok = project_service.reassign(db_session, projekt.id, to_user_id=tibor.id, assigned_by=spravca.id)

    assert projekt.owner_id == tibor.id, "adresát upozornení zostal na pôvodnom manažérovi"
    assert vysledok.notifications_follow_manager is True
    assert vysledok.notifications_blocked_reason is None


def test_notifications_stay_put_when_the_new_manager_has_nowhere_to_get_them(db_session) -> None:
    """⚠️ Ticho by tu bolo horšie než nesprávny adresát.

    Tibor v deň zavedenia tejto funkcie Telegram zapísaný nemal. Keby sa adresát presunul naňho,
    upozornenia by neprišli NIKOMU — z „chodia nesprávnemu človeku“ by sa stalo „nechodia vôbec“.
    Preto sa v takom prípade nechá pôvodný adresát a vráti sa dôvod, ktorý kokpit povie nahlas.
    """
    spravca = _user(db_session, "spravca6")
    tibor = _user(db_session, "tibor6")  # bez Telegramu — presne stav `tibi` 08.09.2026
    nazar = _user(db_session, "nazar6", telegram="222")
    projekt = _project(db_session, nazar)
    projekt.owner_id = nazar.id
    db_session.flush()

    vysledok = project_service.reassign(db_session, projekt.id, to_user_id=tibor.id, assigned_by=spravca.id)

    assert projekt.created_by == tibor.id, "zodpovednosť sa presunúť MUSÍ aj tak"
    assert projekt.owner_id == nazar.id, "upozornenia bez adresáta by sa stratili potichu"
    assert vysledok.notifications_follow_manager is False
    assert vysledok.notifications_blocked_reason is not None
    assert "tibor6" in vysledok.notifications_blocked_reason


def test_the_recipient_written_on_disk_moves_too(tmp_path) -> None:
    """⚠️ Adresát upozornení žije na DVOCH miestach, nie na jednom.

    Okrem stĺpca v databáze (kokpit) je ešte ``TELEGRAM_NOTIFY_CHAT_ID`` v ``.env`` projektu, odkiaľ
    číta hook, ktorým si hlási sám agent. Presunúť len ten prvý = opraviť polovicu: projekt by patril
    novému manažérovi, ale agent by hlásil ďalej starému. Zmerané 08.09.2026 — zápis na disku má
    4 z 9 projektov, čiže to nie je teoretická cesta.
    """
    from backend.services.template_bootstrap import sync_notify_chat_id

    projekt_dir = tmp_path / "nejaky-projekt"
    projekt_dir.mkdir()
    (projekt_dir / ".env").write_text("FOO=bar\nTELEGRAM_NOTIFY_CHAT_ID=stary\nBAZ=qux\n", encoding="utf-8")

    class _P:
        source_path = str(projekt_dir)

    assert sync_notify_chat_id(_P(), "novy") is True
    text = (projekt_dir / ".env").read_text(encoding="utf-8")
    assert "TELEGRAM_NOTIFY_CHAT_ID=novy" in text
    assert "stary" not in text
    assert "FOO=bar" in text and "BAZ=qux" in text, "ostatné riadky sa nesmú dotknúť"


def test_a_project_that_never_wanted_notifications_does_not_start_getting_them(tmp_path) -> None:
    """Zverenie nemá projektu pridávať to, o čo nikdy nestál. Zapisuje sa len tam, kde ten kľúč UŽ je."""
    from backend.services.template_bootstrap import sync_notify_chat_id

    projekt_dir = tmp_path / "bez-upozorneni"
    projekt_dir.mkdir()
    (projekt_dir / ".env").write_text("FOO=bar\n", encoding="utf-8")

    class _P:
        source_path = str(projekt_dir)

    assert sync_notify_chat_id(_P(), "novy") is False
    assert (projekt_dir / ".env").read_text(encoding="utf-8") == "FOO=bar\n"


# ─── Ľudia sa volajú menom (ICCINT-81) ────────────────────────────────────────


def test_people_are_shown_by_name_not_by_login() -> None:
    """Manažér zveruje projekt človeku, nie účtu. Nemá si v hlave prekladať ``tibi`` na Tibora —
    zadal Director 08.09.2026 hneď po prvom zverení."""
    from backend.services.user import person_label

    assert person_label("Tibor", "Rausch", "tibi") == "Tibor Rausch"
    assert person_label("Admin", None, "admin") == "Admin", "chýbajúce priezvisko nie je chyba"
    assert person_label(None, None, "stroj") == "stroj", "prázdny riadok by bol horší než prihlasovacie meno"
    assert person_label("  ", "  ", "stroj") == "stroj", "samé medzery nie sú meno"


def test_the_history_names_people_too() -> None:
    """Vyberiem „Tibor Rausch“ a v histórii pod tým čítam ``tibi`` — to je presne ten rozpor, ktorý
    manažérovi berie istotu, že klikol na správneho človeka."""
    import inspect

    from backend.api.routes import projects as routes

    src = inspect.getsource(routes.read_project_assignments)
    assert "person_label(" in src, "história skladá ľudí z prihlasovacích mien"
    assert "User.username" in src, "prihlasovacie meno musí zostať ako záloha, keď meno nie je vyplnené"


def test_giving_it_again_is_how_a_late_telegram_gets_fixed(db_session) -> None:
    """⚠️ Bez tohto by rada, ktorú kokpit sám dáva, nefungovala.

    Keď sa projekt zverí človeku bez Telegramu, obrazovka povie „doplň mu Telegram a zver projekt
    znova“. Lenže druhé zverenie je presun na TOHO ISTÉHO človeka — a ten sa v histórii zámerne
    nezapisuje. Keby sa na tom riadku funkcia zastavila, adresát upozornení by zostal navždy zlý
    a jediná cesta späť by viedla cez zásah do databázy.
    """
    spravca = _user(db_session, "spravca7")
    tibor = _user(db_session, "tibor7")  # zatiaľ bez Telegramu
    nazar = _user(db_session, "nazar7", telegram="222")
    projekt = _project(db_session, nazar)
    projekt.owner_id = nazar.id
    db_session.flush()

    prve = project_service.reassign(db_session, projekt.id, to_user_id=tibor.id, assigned_by=spravca.id)
    assert prve.notifications_follow_manager is False
    assert projekt.owner_id == nazar.id

    tibor.telegram_chat_id = "333"  # Director medzitým doplnil Telegram
    db_session.flush()

    druhe = project_service.reassign(db_session, projekt.id, to_user_id=tibor.id, assigned_by=spravca.id)

    assert druhe.notifications_follow_manager is True
    assert projekt.owner_id == tibor.id, "opakované zverenie musí dorovnať adresáta upozornení"
    assert len(project_service.assignment_history(db_session, projekt.id)) == 1, (
        "opakované zverenie nesmie pridať druhý riadok do histórie — nič sa nezmenilo na zodpovednosti"
    )

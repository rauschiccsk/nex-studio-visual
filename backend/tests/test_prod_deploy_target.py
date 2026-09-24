"""Ostrá prevádzka zákazníka môže bežať na INOM stroji než kokpit (ICCINT-151).

Rozhodnutie Directora 24.09.2026: nasadenie na ostrú prevádzku má byť to isté kliknutie ako pri
testovacej inštalácii — len smeruje na stroj zákazníka. Žiadny človek na druhej strane, žiadna
druhá vetva nasadzovania (dve vetvy by sa rozišli a jedna by sa prestala skúšať).

Cieľ je údaj PRI ZÁKAZNÍKOVI, nie pri projekte: MAGER je server MÁGERSTAVu a beží na ňom jeho
Inbox aj jeho Manager. Prázdna hodnota znamená „tento stroj".
"""

from __future__ import annotations

from backend.services.orchestrator import _docker_env_for_target


def test_without_a_target_nothing_is_added():
    """Zákazník bez cieľa sa nasadzuje sem — prostredie sa nesmie dotknúť."""
    env = {"PATH": "/usr/bin", "VITE_APP_VERSION": "1.2.2"}

    out = _docker_env_for_target(env, None)

    assert "DOCKER_HOST" not in out
    assert out["VITE_APP_VERSION"] == "1.2.2"


def test_a_target_points_docker_at_that_machine():
    """S cieľom ide ten istý príkaz na druhý stroj — cez Docker, ktorý si spojenie otvorí sám."""
    out = _docker_env_for_target({"PATH": "/usr/bin"}, "mager")

    assert out["DOCKER_HOST"] == "ssh://mager"
    assert out["PATH"] == "/usr/bin", "ostatné prostredie zostáva"


def test_a_blank_target_means_this_machine():
    """Prázdne políčko pri zákazníkovi je bežný stav a NESMIE vyrobiť ``ssh://`` bez stroja —
    taký príkaz by nešiel nikam a chyba by sa hľadala ťažko."""
    for prazdne in ("", "   ", "\t"):
        out = _docker_env_for_target({"PATH": "/usr/bin"}, prazdne)
        assert "DOCKER_HOST" not in out, f"prázdna hodnota {prazdne!r} vyrobila cieľ"


def test_the_target_wins_over_an_inherited_one():
    """Kokpit beží v kontajneri, ktorý môže mať ``DOCKER_HOST`` zdedený z prostredia. Cieľ zákazníka
    je rozhodnutie, nie návrh — nesmie ho prebiť niečo, čo sa tam ocitlo samo."""
    out = _docker_env_for_target({"DOCKER_HOST": "unix:///var/run/docker.sock"}, "mager")

    assert out["DOCKER_HOST"] == "ssh://mager"


def test_the_original_environment_is_not_mutated():
    """Prostredie sa vracia nové. Dopísať do toho, čo dostaneme, by zmenilo aj beh, ktorý s nasadením
    nesúvisí — a taká chyba sa prejaví až inde a neskôr."""
    povodne = {"PATH": "/usr/bin"}

    _docker_env_for_target(povodne, "mager")

    assert "DOCKER_HOST" not in povodne

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


def test_the_readiness_check_asks_the_same_machine_it_deployed_to(monkeypatch):
    """Nasadenie a overenie musia hovoriť s TÝM ISTÝM strojom.

    Overenie „appka naozaj slúži" sa robí príkazom ``docker compose exec`` — teda cez Docker. Keby
    nasadenie išlo na MAGER a overenie sa pýtalo tunajšieho Dockera, kokpit by hlásil úspech podľa
    stroja, na ktorom sa nič nenasadilo. To je horšie než neoverovať vôbec.
    """
    import asyncio

    from backend.services import orchestrator as O

    videne: list[dict] = []

    async def fake_step(cmd, timeout, env=None):  # noqa: ARG001
        videne.append(env or {})
        return 0, "HTTP 200"

    monkeypatch.setattr(O, "_compose_smoke_step", fake_step)

    asyncio.run(
        O._await_http_ready(
            ["docker", "compose", "-f", "/x/docker-compose.yml"],
            "backend",
            8000,
            host="localhost",
            path="/api",
            env={"DOCKER_HOST": "ssh://mager"},
        )
    )

    assert videne, "overenie nespustilo ani jeden príkaz"
    assert videne[0].get("DOCKER_HOST") == "ssh://mager", "overenie sa pýtalo iného stroja, než na ktorý sa nasadzovalo"


def test_the_readiness_check_builds_its_environment_from_the_target():
    """Miesto, kde sa cieľ prekladá na prostredie pre overenie. Vlastná stráž preto, že pri úprave
    sa práve tento riadok stratí najľahšie — a strata sa neprejaví inak než tichým overovaním
    nesprávneho stroja."""
    from backend.services.orchestrator import _verify_env_for_target

    assert _verify_env_for_target(None) is None, "bez cieľa sa overuje tu — netreba nič stavať"
    assert _verify_env_for_target("  ") is None, "prázdna hodnota nie je cieľ"

    env = _verify_env_for_target("mager")
    assert env is not None and env["DOCKER_HOST"] == "ssh://mager"


# -- Priečinky s dátami na cieli (ICCINT-151) ----------------------------------


def test_the_target_directories_are_prepared_through_docker_not_a_shell():
    """Kľúč kokpitu je na cieli obmedzený na ``docker system dial-stdio`` — shell ním získať NEJDE,
    a to je zámer: v tom istom kontajneri bežia stavby projektov. Priečinky pre dáta zákazníka preto
    nemôžu vzniknúť cez ``ssh mkdir``; musia vzniknúť cez Docker, ktorý je jediné, s čím ten kľúč vie
    hovoriť.
    """
    from pathlib import Path

    from backend.services.orchestrator import _remote_mkdir_cmd

    cmd = _remote_mkdir_cmd(Path("/opt/customers/mager/nex-inbox"), ["originals", "exports"])

    assert cmd[0] == "docker" and "run" in cmd and "--rm" in cmd
    assert "ssh" not in cmd, "priečinky sa nesmú robiť cez shell — kľúč naň nemá právo"
    spojene = " ".join(cmd)
    assert "/opt/customers/mager/nex-inbox:/target" in spojene, "priečinok inštalácie sa nepripojil"
    assert "originals" in spojene and "exports" in spojene


def test_the_prepared_directories_belong_to_the_application_user():
    """Priečinok vyrobený Dockerom patrí správcovi systému. Aplikácia doň potom nezapíše a chyba sa
    prejaví až pri prvej faktúre — preto sa vlastník nastavuje hneď. Oba stroje majú toho istého
    používateľa pod číslom 1000 (overené 24.09.2026)."""
    from pathlib import Path

    from backend.services.orchestrator import _remote_mkdir_cmd

    spojene = " ".join(_remote_mkdir_cmd(Path("/opt/customers/mager/nex-inbox"), ["originals"]))

    assert "chown" in spojene and "1000:1000" in spojene


def test_nothing_outside_the_instance_directory_is_touched():
    """Zákaznícke dáta iných inštalácií sú na tom istom stroji. Pripojiť sa smie VÝHRADNE priečinok
    tejto inštalácie — nie ``/opt/customers`` a nie koreň."""
    from pathlib import Path

    from backend.services.orchestrator import _remote_mkdir_cmd

    spojene = " ".join(_remote_mkdir_cmd(Path("/opt/customers/mager/nex-inbox"), ["originals"]))

    assert " -v /:/" not in spojene and "/opt/customers:/" not in spojene

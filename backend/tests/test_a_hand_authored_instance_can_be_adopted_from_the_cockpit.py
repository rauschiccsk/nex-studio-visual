"""Ručne písanú inštaláciu možno prevziať z kokpitu — a nič sa pritom nestratí (ICCINT-102).

**Čo tomu predchádzalo.** Provisioner odmieta zapisovať do priečinka, ktorý sám nevygeneroval. Poistka
je správna: na ANDROSe je trinásť ručne písaných inštalácií vrátane ostrej pošty MÁGERSTAVU a
spoločného Traefiku. Jediná cesta ďalej však viedla cez terminál (``uat-deploy.py --adopt``).

Director 10.09.2026: *„Pracujeme nad vývojovým ekosystémom, ktorý má zabezpečiť KOMPLETNÝ workflow od
návrhu až po nasadenie na UAT a na PROD. Také terminálové príkazy nie sú pre mňa riešenie.“*

**Prečo sa pôvodná obava dá splniť inak.** Rozhodnutie z 28.07.2026 stálo na tom, že vpísaním hlavičky
sa zničí jediný dôkaz, že súbor bol písaný ručne. Tu sa nič nezničí — ručná práca sa **odloží** vedľa
ako ``.pre-nex-studio``, presne ako pri prevzatí projektu (ICCINT-87).

⚠️ Čo tieto stráže NEDOVOLIA zmeniť: klik na „Nasadiť“ sa k cudziemu priečinku nesmie dostať nikdy,
prevzatie musí byť odpísané (nie odkliknuté) a nikdy nesmie byť hromadné.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from backend.services import instance_adoption

RUCNY_COMPOSE = """# NEX Manager v1.0.0 — UAT pre MÁGERSTAV (uat-mager-manager.isnex.eu)
# Ručne písané, spravidla živé zákaznícke nasadenie.
name: uat-mager-manager
services:
  backend:
    image: nexmanager-backend:1.0.0
"""
RUCNY_ENV = "POSTGRES_PASSWORD=nechytat\nSMTP_HOST=posta.magerstav.sk\n"


def _rucna_instalacia(tmp_path: Path) -> Path:
    d = tmp_path / "mager" / "nex-manager"
    d.mkdir(parents=True)
    (d / "docker-compose.yml").write_text(RUCNY_COMPOSE, encoding="utf-8")
    (d / ".env").write_text(RUCNY_ENV, encoding="utf-8")
    return d


# ── náhľad: rozhodovať sa z faktov, nie z odhadu ──────────────────────────────


def test_the_preview_says_what_will_be_set_aside_and_what_will_not(tmp_path) -> None:
    """⚠️ Terminálový ``--dry-run`` to isté ukazoval len tomu, kto vie napísať príkaz."""
    d = _rucna_instalacia(tmp_path)
    (d / "poznamky.txt").write_text("moje poznámky\n", encoding="utf-8")

    n = instance_adoption.preview(d)

    assert n.exists and not n.already_ours
    assert sorted(n.set_aside) == [
        (".env", ".env.pre-nex-studio"),
        ("docker-compose.yml", "docker-compose.yml.pre-nex-studio"),
    ]
    assert n.untouched == ["poznamky.txt"], "náhľad zamlčal súbor, ktorého sa prevzatie nedotkne"


def test_the_preview_asks_for_a_phrase_that_names_the_customer(tmp_path) -> None:
    """Holé „nex-manager“ je rovnaké pre troch zákazníkov — odpísať sa dá bez pozretia, koho to je."""
    d = _rucna_instalacia(tmp_path)

    assert instance_adoption.preview(d).confirmation_phrase == "mager/nex-manager"


def test_an_instance_we_already_manage_has_nothing_to_adopt(tmp_path) -> None:
    """Náš vlastný priečinok sa nepreberá — inak by sa z prevzatia stal druhý spôsob nasadenia."""
    d = _rucna_instalacia(tmp_path)
    (d / "docker-compose.yml").write_text(
        f"# {instance_adoption.uat_provisioner.GENERATED_BY_MARKER}\nname: uat-mager-manager\n",
        encoding="utf-8",
    )

    n = instance_adoption.preview(d)

    assert n.already_ours
    assert n.set_aside == [], "nášmu vlastnému priečinku sa nemá čo odkladať"


def test_a_missing_directory_is_an_ordinary_answer(tmp_path) -> None:
    """Prvé nasadenie k zákazníkovi je bežný prípad — nie chyba, ktorú treba hlásiť."""
    n = instance_adoption.preview(tmp_path / "niet" / "nic")

    assert not n.exists and not n.already_ours and n.set_aside == []


# ── odloženie: dôkaz zostáva, krok je vratný ──────────────────────────────────


def test_setting_aside_keeps_the_hand_written_files_byte_for_byte(tmp_path) -> None:
    """⚠️ Jadro veci. Toto je odpoveď na obavu z 28.07.2026 — dôkaz o ručnej práci sa NEZNIČÍ."""
    d = _rucna_instalacia(tmp_path)

    vytvorene = instance_adoption.set_aside_hand_authored(d)

    assert sorted(vytvorene) == [".env.pre-nex-studio", "docker-compose.yml.pre-nex-studio"]
    assert (d / "docker-compose.yml.pre-nex-studio").read_text(encoding="utf-8") == RUCNY_COMPOSE
    assert (d / ".env.pre-nex-studio").read_text(encoding="utf-8") == RUCNY_ENV


def test_the_original_stays_in_place_until_the_render_replaces_it(tmp_path) -> None:
    """⚠️ Kopíruje sa, nepresúva. Keby sme súbor odsunuli a render potom zlyhal, bežiaca inštalácia
    by ostala bez svojho popisu — a to je horšie než neprevzatá inštalácia."""
    d = _rucna_instalacia(tmp_path)

    instance_adoption.set_aside_hand_authored(d)

    assert (d / "docker-compose.yml").read_text(encoding="utf-8") == RUCNY_COMPOSE
    assert (d / ".env").read_text(encoding="utf-8") == RUCNY_ENV


def test_a_second_adoption_never_overwrites_the_first_backup(tmp_path) -> None:
    """Inak by druhé prevzatie uložilo vedľa NAŠU kópiu a originál by zmizol (tá istá pasca ako ICCINT-87)."""
    d = _rucna_instalacia(tmp_path)
    instance_adoption.set_aside_hand_authored(d)
    (d / "docker-compose.yml").write_text("# už naša verzia\n", encoding="utf-8")

    znova = instance_adoption.set_aside_hand_authored(d)

    assert znova == [], "druhé odloženie si vypýtalo miesto, ktoré už bolo obsadené"
    assert (d / "docker-compose.yml.pre-nex-studio").read_text(encoding="utf-8") == RUCNY_COMPOSE


def test_nothing_to_set_aside_is_not_an_error(tmp_path) -> None:
    """Prázdny priečinok sa preberať nemusí — a nesmie z toho byť pád."""
    d = tmp_path / "mager" / "nex-nove"
    d.mkdir(parents=True)

    assert instance_adoption.set_aside_hand_authored(d) == []


# ── čo sa NESMIE zmeniť ───────────────────────────────────────────────────────


def test_the_deploy_button_still_cannot_reach_a_foreign_directory() -> None:
    """⚠️ Rozhodnutie Directora z 28.07.2026, ktoré NEPADLO: klik na „Nasadiť“ sa k ručne písanému
    nasadeniu nesmie dostať. Prevzatie je oddelená cesta s vlastným potvrdením — nie prepínač pri nej.

    Tá istá stráž stojí aj v ``tests/test_provisioner_overwrite_guard.py``; tu je zámerne druhýkrát,
    lebo práve tento súbor pridal cestu, ktorá override používa. Kto ju raz presunie do ``deploy.py``,
    narazí na ňu tu.
    """
    from backend.services import deploy as deploy_service

    assert "allow_overwrite" not in inspect.getsource(deploy_service)


def test_adoption_preserves_secrets_by_construction() -> None:
    """Databáza v tom priečinku beží so starým heslom — čerstvé tajomstvá by ju odrezali.

    Meria sa ZDROJ, nie beh: rotácia sa v tejto ceste nesmie dať zapnúť ani omylom.
    """
    zdroj = inspect.getsource(instance_adoption.adopting_deploy_runner)

    assert "rotate_secrets=force_fresh" in zdroj
    assert "rotate_secrets=True" not in zdroj


def test_one_call_adopts_exactly_one_instance() -> None:
    """Hromadné prevzatie Director 28.07.2026 zamietol po troch nezávislých previerkach.

    ``set_aside_hand_authored`` berie JEDEN priečinok a nikde neiteruje cez súrodencov — kto by chcel
    hromadné prevzatie, musí zmeniť podpis, a tým narazí na túto stráž.
    """
    parametre = inspect.signature(instance_adoption.set_aside_hand_authored).parameters

    # ``from __future__ import annotations`` robí z anotácií reťazce — porovnáva sa teda meno typu.
    assert list(parametre) == ["instance_dir"]
    assert parametre["instance_dir"].annotation in (Path, "Path")


@pytest.mark.parametrize("meno", ["docker-compose.yml", ".env"])
def test_both_hand_written_files_are_covered(meno: str) -> None:
    """``.env`` je ten súbor, ktorého ručne nastavené hodnoty (SMTP, párovaný kľúč) nie sú nikde inde."""
    assert meno in instance_adoption.HAND_AUTHORED_FILES


# ── vykonávateľ sa naozaj SPUSTÍ ──────────────────────────────────────────────
#
# ⚠️ Tu bola diera v mojich vlastných strážach. Overil som čisté funkcie aj dialóg, ale
# ``adopting_deploy_runner`` som nikdy nespustil — a v ňom stálo ``result.url``, ktoré na
# ``ProvisionResult`` neexistuje. Zistilo sa to až v ostrej prevádzke a najhorším možným spôsobom:
# priečinok sa PREVZAL, kontajnery nabehli, ale volanie skončilo chybou 500 — takže Manažér videl
# „Prevzatie zlyhalo“, v evidencii nebolo nič, a na disku bolo hotovo.
#
# Stráž preto vykonávateľa spustí celý, s atrapami provisionera aj nasadenia.


class _FakeProvisionResult:
    """To, čo provisioner naozaj vracia — a hlavne to, čo NEvracia (``url`` na ňom nie je)."""

    def __init__(self) -> None:
        self.fe_service = "frontend"
        self.warnings = ["skúšobné upozornenie"]


@pytest.mark.asyncio
async def test_the_runner_survives_a_real_call_and_builds_the_url_itself(tmp_path, monkeypatch) -> None:
    """⚠️ Jadro: vykonávateľ musí prebehnúť CELÝ. Prvé znenie padlo na ``result.url``."""
    from backend.services import instance_adoption as ia

    d = _rucna_instalacia(tmp_path)
    monkeypatch.setattr(ia, "instance_dir_for", lambda **_: d)
    monkeypatch.setattr(ia.uat_provisioner, "provision_uat", lambda *a, **k: _FakeProvisionResult())
    monkeypatch.setattr(ia.uat_provisioner, "derive_uat_slug", lambda _: "manager")

    async def _fake_uat_deploy(*a, **k):
        return True, "OK"

    from backend.services import orchestrator

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _fake_uat_deploy)

    ok, detail, url = await ia.adopting_deploy_runner(
        project_slug="nex-manager", uat_slug="mager-uat", version_number="1.1.0", force_fresh=False
    )

    assert ok is True
    assert url and url.startswith("https://"), f"vykonávateľ nezložil adresu: {url!r}"
    assert "mager-manager" in url, f"adresa nemieri na inštaláciu zákazníka: {url}"


@pytest.mark.asyncio
async def test_the_runner_reports_what_it_set_aside(tmp_path, monkeypatch) -> None:
    """Manažér sa musí dozvedieť, že jeho ručná práca leží vedľa — inak ju nikdy nenájde."""
    from backend.services import instance_adoption as ia
    from backend.services import orchestrator

    d = _rucna_instalacia(tmp_path)
    monkeypatch.setattr(ia, "instance_dir_for", lambda **_: d)
    monkeypatch.setattr(ia.uat_provisioner, "provision_uat", lambda *a, **k: _FakeProvisionResult())
    monkeypatch.setattr(ia.uat_provisioner, "derive_uat_slug", lambda _: "manager")

    async def _fake_uat_deploy(*a, **k):
        return True, "OK"

    monkeypatch.setattr(orchestrator, "_run_uat_deploy", _fake_uat_deploy)

    _ok, detail, _url = await ia.adopting_deploy_runner(
        project_slug="nex-manager", uat_slug="mager-uat", version_number="1.1.0", force_fresh=False
    )

    assert "docker-compose.yml.pre-nex-studio" in detail
    assert ".env.pre-nex-studio" in detail
    assert "skúšobné upozornenie" in detail, "upozornenia provisionera sa cestou stratili"


@pytest.mark.asyncio
async def test_a_failed_provision_does_not_pretend_to_have_adopted(tmp_path, monkeypatch) -> None:
    """Keď render zlyhá, vykonávateľ to povie — a nevymyslí si adresu bežiacej appky."""
    from backend.services import instance_adoption as ia

    d = _rucna_instalacia(tmp_path)
    monkeypatch.setattr(ia, "instance_dir_for", lambda **_: d)

    def _padne(*_a, **_k):
        raise ValueError("nedá sa")

    monkeypatch.setattr(ia.uat_provisioner, "provision_uat", _padne)
    monkeypatch.setattr(ia.uat_provisioner, "derive_uat_slug", lambda _: "manager")

    ok, detail, url = await ia.adopting_deploy_runner(
        project_slug="nex-manager", uat_slug="mager-uat", version_number="1.1.0", force_fresh=False
    )

    assert ok is False and url is None
    assert "nedá sa" in detail

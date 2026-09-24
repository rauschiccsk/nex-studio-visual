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
from backend.tests._adoption_render import render_request

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

    n = instance_adoption.preview(d, render=render_request(tmp_path))

    assert n.exists and not n.already_ours
    assert sorted(n.set_aside) == [
        (".env", ".env.pre-nex-studio"),
        ("docker-compose.yml", "docker-compose.yml.pre-nex-studio"),
    ]
    assert n.untouched == ["poznamky.txt"], "náhľad zamlčal súbor, ktorého sa prevzatie nedotkne"


def test_the_preview_asks_for_a_phrase_that_names_the_customer(tmp_path) -> None:
    """Holé „nex-manager“ je rovnaké pre troch zákazníkov — odpísať sa dá bez pozretia, koho to je."""
    d = _rucna_instalacia(tmp_path)

    assert instance_adoption.preview(d, render=render_request(tmp_path)).confirmation_phrase == "mager/nex-manager"


def test_an_instance_we_already_manage_has_nothing_to_adopt(tmp_path) -> None:
    """Náš vlastný priečinok sa nepreberá — inak by sa z prevzatia stal druhý spôsob nasadenia."""
    d = _rucna_instalacia(tmp_path)
    (d / "docker-compose.yml").write_text(
        f"# {instance_adoption.uat_provisioner.GENERATED_BY_MARKER}\nname: uat-mager-manager\n",
        encoding="utf-8",
    )

    n = instance_adoption.preview(d, render=render_request(tmp_path))

    assert n.already_ours
    assert n.set_aside == [], "nášmu vlastnému priečinku sa nemá čo odkladať"


def test_a_missing_directory_is_an_ordinary_answer(tmp_path) -> None:
    """Prvé nasadenie k zákazníkovi je bežný prípad — nie chyba, ktorú treba hlásiť."""
    n = instance_adoption.preview(tmp_path / "niet" / "nic", render=render_request(tmp_path))

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


# ── ICCINT-130: prevzatie nesmie stratiť to, čo vie LEN tá bežiaca inštalácia ──
#
# Zmerané 14.09.2026 na UAT MÁGERSTAVU. Ručne písaný compose niesol riadok, ktorý generátor nemá
# odkiaľ vziať — projektový docker-compose.yml o ňom nevie a v evidencii zákazníkov naň nie je stĺpec:
#
#     /mnt/mager-edocs-inbox-uat:/var/lib/inbox/genesis-out     ← priečinok, kam Genesis ukladá faktúry
#     subnet: 192.168.48.0/24                                   ← Dockeru došli automatické siete
#     mail.isnex.eu:192.168.55.250                              ← poštový hostiteľ
#
# Prevzatie by prešlo, ohlásilo úspech a appka by prestala vidieť faktúry. Ticho. Trinásť ručne
# písaných inštalácií na ANDROSe nie je nedbalosť — je to zoznam miest, kde skutočnosť nesadla do
# predstavy kokpitu.

_RUCNY_COMPOSE = """\
# NEX Inbox v1.4.0 — UAT pre MÁGERSTAV
name: uat-mager-inbox

networks:
  inbox-net:
    driver: bridge
    ipam:
      config:
        - subnet: 192.168.48.0/24

services:
  postgres:
    image: postgres:16-alpine
    volumes:
      - postgres-data:/var/lib/postgresql/data
  backend:
    image: nex-inbox-backend:v1.4.0
    extra_hosts:
      - "mail.isnex.eu:192.168.55.250"
    volumes:
      - ./originals:/var/lib/inbox/originals
      - /mnt/mager-edocs-inbox-uat:/var/lib/inbox/genesis-out
  frontend:
    image: nex-inbox-frontend:v1.4.0

volumes:
  postgres-data:
"""


def _magerstav_instalacia(tmp_path):
    """Ručne písaná inštalácia, presne v tvare toho, čo 14.09.2026 bežalo pre MÁGERSTAV."""
    d = tmp_path / "mager" / "nex-inbox"
    d.mkdir(parents=True)
    (d / "docker-compose.yml").write_text(_RUCNY_COMPOSE, encoding="utf-8")
    (d / ".env").write_text("POSTGRES_PASSWORD=x\n", encoding="utf-8")
    return d


def test_the_facts_only_the_live_instance_knows_are_read_off_it(tmp_path):
    """Čítať ich z bežiaceho súboru je jediný spôsob, ktorý sa nemôže rozísť so skutočnosťou — a
    nevyžaduje, aby ich niekto opisoval do formulára. Laik pripojenie priečinka neodpíše."""
    from backend.services import uat_provisioner

    fakty = uat_provisioner.read_instance_facts(_magerstav_instalacia(tmp_path))

    assert "/mnt/mager-edocs-inbox-uat:/var/lib/inbox/genesis-out" in fakty.host_mounts.get("backend", []), (
        "pripojenie hostiteľského priečinka sa nenašlo — presne to by sa pri prevzatí stratilo"
    )
    assert "mail.isnex.eu:192.168.55.250" in fakty.extra_hosts
    assert fakty.network_subnets.get("inbox-net") == "192.168.48.0/24"
    # OBRÁTENÉ 15.09.2026. Pôvodne tu stálo, že relatívny zväzok „generátor vie sám". Zmerané na
    # skutočnej inštalácii MÁGERSTAVU: zdrojový projekt mountuje ./claude-config, kým bežiaca
    # inštalácia ./originals a ./exports — priečinky s originálmi faktúr a exportovaným XML.
    # Vykreslenie zo zdroja by ich zahodilo a obsah by sa stratil pri každom ďalšom nasadení.
    assert "./originals:/var/lib/inbox/originals" in fakty.host_mounts.get("backend", []), (
        "priečinok inštalácie sa musí prenášať — zdroj o ňom nevie"
    )
    assert "postgres-data:/var/lib/postgresql/data" not in fakty.host_mounts.get("postgres", []), (
        "pomenovaný zväzok nie je pripojenie hostiteľského priečinka"
    )


def test_the_preview_says_what_would_be_LOST_not_which_files_move(tmp_path):
    """Náhľad hovoril, ktoré SÚBORY sa odložia bokom. To nie je údaj, na základe ktorého sa dá
    rozhodnúť — Manažér potrebuje vedieť, čo v novom súbore NEBUDE. Pri takom náhľade by sa chyba
    zo 14.09.2026 nedala kliknúť."""
    from backend.services import instance_adoption

    nahlad = instance_adoption.preview(_magerstav_instalacia(tmp_path), render=render_request(tmp_path))

    prenesie = " ".join(nahlad.carried_over)
    assert "/mnt/mager-edocs-inbox-uat" in prenesie, f"náhľad nemenuje pripojenie: {nahlad.carried_over}"
    assert "192.168.48.0/24" in prenesie, f"náhľad nemenuje podsieť: {nahlad.carried_over}"
    assert "mail.isnex.eu" in prenesie, f"náhľad nemenuje poštového hostiteľa: {nahlad.carried_over}"


def test_adoption_is_refused_when_something_would_be_lost(tmp_path):
    """Tá istá zásada, ktorá práve prešla do NEX Inboxu ako R16: akcia sa neponúkne tam, kde nemôže
    uspieť. Úspešne vyzerajúce prevzatie, po ktorom appka oslepne, je horšie než odmietnutie."""
    from backend.services import instance_adoption

    d = _magerstav_instalacia(tmp_path)
    (d / "docker-compose.yml").write_text(
        _RUCNY_COMPOSE.replace("  backend:", "  backend:\n    cap_add:\n      - NET_ADMIN\n"), encoding="utf-8"
    )

    nahlad = instance_adoption.preview(d, render=render_request(tmp_path))

    assert nahlad.blocking, "nepreneseľná vlastnosť sa musí ohlásiť, nie stratiť"
    assert not nahlad.can_adopt, "prevzatie sa nesmie ponúknuť, keď by niečo zmazalo"
    assert any("cap_add" in v for v in nahlad.blocking), nahlad.blocking


def test_a_plain_instance_with_nothing_special_is_still_adoptable(tmp_path):
    """Poistka proti tomu, aby sa z opravy stala nová prekážka. Inštalácia bez zákazníckych
    zvláštností sa prevziať dá — inak by sa trinásť priečinkov zmenilo na trinásť slepých ulíc."""
    from backend.services import instance_adoption

    d = tmp_path / "icc" / "nex-demo"
    d.mkdir(parents=True)
    (d / "docker-compose.yml").write_text(
        "name: uat-icc-demo\nservices:\n  backend:\n    image: demo:v1\n", encoding="utf-8"
    )
    (d / ".env").write_text("X=1\n", encoding="utf-8")

    nahlad = instance_adoption.preview(d, render=render_request(tmp_path))

    assert nahlad.can_adopt, nahlad.blocking
    assert nahlad.blocking == []


def test_the_render_actually_carries_the_facts_the_preview_promised(tmp_path):
    """Náhľad sľubuje, že sa tie údaje prenesú. Ak by ich vykreslenie nenieslo, sľub by bol horší než
    mlčanie — Manažér by potvrdil prevzatie práve preto, že mu obrazovka povedala, že sa nič nestratí.

    Toto je tá polovica opravy, na ktorej záleží. Zvyšok je len o tom, aby to bolo vidieť.
    """
    from backend.services import uat_provisioner

    fakty = uat_provisioner.read_instance_facts(_magerstav_instalacia(tmp_path))
    # Sieť sa v zdroji volá INAK než v ručnej inštalácii — presne ako v skutočnosti: nex-inbox
    # deklaruje `inbox-dev-net`, MÁGERSTAV má `inbox-net`. Párovanie podľa mena by tu podsieť
    # stratilo, a strata podsiete na ANDROSe znamená, že sa sieť vôbec nepridelí.
    zdroj = {
        "services": {
            "postgres": {"image": "postgres:16-alpine"},
            "backend": {"image": "demo-be", "volumes": ["./originals:/var/lib/inbox/originals"]},
            "frontend": {"image": "demo-fe"},
        },
        "networks": {"inbox-dev-net": None},
    }

    compose = uat_provisioner.build_uat_compose(
        slug="mager-inbox",
        project="nex-inbox",
        project_path=tmp_path,
        source=zdroj,
        roles={"backend": "backend", "frontend": "frontend", "db": "postgres"},
        db_user="u",
        db_name="d",
        environment="uat",
        customer_slug="mager",
        app="nex-inbox",
        preserved_facts=fakty,
    )

    be_volumes = [str(v) for v in compose["services"]["backend"].get("volumes", [])]
    assert "/mnt/mager-edocs-inbox-uat:/var/lib/inbox/genesis-out" in be_volumes, (
        f"pripojenie na Genesis sa vo vykreslenom súbore stratilo — {be_volumes}"
    )
    assert "./originals:/var/lib/inbox/originals" in be_volumes, "pôvodné zväzky sa nesmú zahodiť"

    be_hosts = [str(h) for h in compose["services"]["backend"].get("extra_hosts", [])]
    assert "mail.isnex.eu:192.168.55.250" in be_hosts

    siete = compose.get("networks") or {}
    podsiete = [
        e.get("subnet")
        for net in siete.values()
        if isinstance(net, dict)
        for e in ((net.get("ipam") or {}).get("config") or [])
        if isinstance(e, dict)
    ]
    assert "192.168.48.0/24" in podsiete, (
        f"ručne pridelená podsieť sa stratila — Dockeru došli automatické siete, preto tam bola: {siete}"
    )


def test_the_render_without_facts_is_unchanged(tmp_path):
    """Poistka, že sa z prenášania nestane nová vetva pre KAŽDÉ nasadenie. Bez zákazníckych údajov
    musí vykreslenie vyzerať presne ako dovtedy — inak by oprava jednej inštalácie menila všetky."""
    from backend.services import uat_provisioner

    zdroj = {"services": {"backend": {"image": "demo-be"}, "frontend": {"image": "demo-fe"}}}
    spolocne = dict(
        slug="icc-demo",
        project="nex-demo",
        project_path=tmp_path,
        source=zdroj,
        roles={"backend": "backend", "frontend": "frontend", "db": None},
        db_user="u",
        db_name="d",
        environment="uat",
        customer_slug="icc",
        app="nex-demo",
    )

    bez = uat_provisioner.build_uat_compose(**spolocne)
    s_prazdnymi = uat_provisioner.build_uat_compose(
        **spolocne, preserved_facts=uat_provisioner.InstanceFacts({}, [], {}, {})
    )

    assert bez == s_prazdnymi, "prázdne fakty nesmú vykreslenie zmeniť ani o písmeno"


def test_an_ambiguous_subnet_stops_loudly_instead_of_being_guessed(tmp_path):
    """Keď sa nedá určiť, ktorej sieti podsieť patrí, hádať sa nesmie. Zlé priradenie vyzerá ako
    úspešné nasadenie a prejaví sa až tým, že sa stack nerozbehne — a to sa číta ako „deploy sa
    pokazil", nie ako „prevzatie zahodilo riadok"."""
    import pytest

    from backend.services import uat_provisioner

    fakty = uat_provisioner.InstanceFacts({}, [], {"a": "10.1.0.0/24", "b": "10.2.0.0/24"}, {})
    with pytest.raises(ValueError, match="nedá sa jednoznačne určiť"):
        uat_provisioner.build_uat_compose(
            slug="icc-demo",
            project="nex-demo",
            project_path=tmp_path,
            source={"services": {"backend": {"image": "x"}}, "networks": {"n1": None, "n2": None}},
            roles={"backend": "backend", "frontend": None, "db": None},
            db_user="u",
            db_name="d",
            environment="uat",
            customer_slug="icc",
            app="nex-demo",
            preserved_facts=fakty,
        )


def test_the_instances_own_data_directories_are_carried_too(tmp_path):
    """Diera vo vlastnej oprave, nájdená 15.09.2026 pri prvom ostrom použití na UAT MAGERSTAVU.

    ICCINT-130 niesol len ABSOLUTNE pripojenia s odovodnenim, ze zvazok relativny k priecinku
    instalacie generator vie sam. Pri tejto instalacii to NEPLATI: zdrojovy projekt ma
    ``./claude-config``, kym beziaca instalacia ma ``./originals`` a ``./exports`` — teda priecinky,
    kam appka uklada originaly faktur a vyexportovane XML.

    Vykreslenie by ich zahodilo, cesty v kontajneri by zostali bez pripojenia na disk a obsah by sa
    stratil pri kazdom dalsom nasadeni. Nie hlucne — ticho.

    Pomer rizik rozhoduje jednoznacne: niest navyse jeden prazdny priecinok stoji nic, stratit
    faktury stoji zakaznika.
    """
    from backend.services import uat_provisioner

    d = tmp_path / "mager" / "nex-inbox"
    d.mkdir(parents=True)
    (d / "docker-compose.yml").write_text(
        "name: uat-mager-inbox\n"
        "services:\n"
        "  backend:\n"
        "    image: x\n"
        "    volumes:\n"
        "      - ./originals:/var/lib/inbox/originals\n"
        "      - ./exports:/var/lib/inbox/exports\n"
        "      - /mnt/mager-edocs-inbox-uat:/var/lib/inbox/genesis-out\n"
        "      - pg-data:/var/lib/postgresql/data\n",
        encoding="utf-8",
    )

    fakty = uat_provisioner.read_instance_facts(d)
    be = fakty.host_mounts.get("backend", [])

    assert "./originals:/var/lib/inbox/originals" in be, f"priecinok s originalmi by sa stratil — {be}"
    assert "./exports:/var/lib/inbox/exports" in be
    assert "/mnt/mager-edocs-inbox-uat:/var/lib/inbox/genesis-out" in be
    assert not any("pg-data" in v for v in be), "pomenovany zvazok NIE JE pripojenie priecinka — Docker ho spravuje sam"


def test_the_live_network_name_is_kept_so_the_subnet_does_not_collide(tmp_path):
    """Zlyhanie z ostrej prevadzky 15.09.2026, hned po prvom prevzati:

        failed to create network uat-mager-inbox_inbox-dev-net:
        invalid pool request: Pool overlaps with other one on this address space

    ICCINT-130 prenieslo HODNOTU podsiete, ale nie MENO siete, na ktorej visi. Vykreslenie siet
    zaroven premenuje (zdrojovy projekt ju vola ``inbox-dev-net``, instalacia ``inbox-net``), takze
    vznikla poziadavka na NOVU siet s uz obsadenou adresou — a Docker ju spravne odmietol.

    Meno siete je rovnaky zakaznicky udaj ako jej adresa. Premenovat ju pri prevzati nie je nicim
    odovodnene: stara siet by osirela a nova by sa s nou bila o adresu.
    """
    from backend.services import uat_provisioner as P

    d = tmp_path / "mager" / "nex-inbox"
    d.mkdir(parents=True)
    (d / "docker-compose.yml").write_text(
        "name: uat-mager-inbox\n"
        "networks:\n"
        "  inbox-net:\n"
        "    ipam:\n"
        "      config:\n"
        "        - subnet: 192.168.48.0/24\n"
        "services:\n"
        "  backend:\n"
        "    image: x\n"
        "    networks: [inbox-net]\n",
        encoding="utf-8",
    )
    fakty = P.read_instance_facts(d)

    novy = P.build_uat_compose(
        slug="mager",
        project="nex-inbox",
        project_path=tmp_path,
        source={
            "services": {"backend": {"image": "y", "networks": ["inbox-dev-net"]}},
            "networks": {"inbox-dev-net": None},
        },
        roles={"backend": "backend", "frontend": None, "db": None},
        db_user="u",
        db_name="d",
        environment="uat",
        customer_slug="mager",
        app="inbox",
        preserved_facts=fakty,
    )

    siete = [n for n in (novy.get("networks") or {}) if n != P.PROXY_NETWORK]
    assert siete == ["inbox-net"], f"siet sa premenovala a bude sa bit o adresu: {siete}"

    be = (novy.get("services") or {})["backend"]
    assert "inbox-net" in (be.get("networks") or []), f"sluzba ukazuje na neexistujucu siet: {be.get('networks')}"
    assert "inbox-dev-net" not in (be.get("networks") or [])

    ipam = (novy["networks"]["inbox-net"] or {}).get("ipam") or {}
    assert any(e.get("subnet") == "192.168.48.0/24" for e in (ipam.get("config") or [])), ipam


def test_a_matching_network_name_is_left_alone(tmp_path):
    """Poistka: ked sa mena zhoduju, nema sa co premenuvat a sprava zostava ako bola."""
    from backend.services import uat_provisioner as P

    d = tmp_path / "icc" / "demo"
    d.mkdir(parents=True)
    (d / "docker-compose.yml").write_text(
        "name: uat-icc-demo\nnetworks:\n  demo-net:\n    ipam:\n      config:\n"
        "        - subnet: 10.9.0.0/24\nservices:\n  backend:\n    image: x\n    networks: [demo-net]\n",
        encoding="utf-8",
    )
    novy = P.build_uat_compose(
        slug="icc",
        project="demo",
        project_path=tmp_path,
        source={"services": {"backend": {"image": "y", "networks": ["demo-net"]}}, "networks": {"demo-net": None}},
        roles={"backend": "backend", "frontend": None, "db": None},
        db_user="u",
        db_name="d",
        environment="uat",
        customer_slug="icc",
        app="demo",
        preserved_facts=P.read_instance_facts(d),
    )
    assert [n for n in novy["networks"] if n != P.PROXY_NETWORK] == ["demo-net"]

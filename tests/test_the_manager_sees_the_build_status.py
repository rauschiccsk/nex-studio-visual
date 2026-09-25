"""Manažér vidí stav zostavenia priebežne, nie až na bráne (ICCINT-129, druhá polovica).

**Čo tomu predchádzalo.** Brána Verifikácie si pýtala od GitHubu JEDEN beh (`--limit 1`) a na repozitári
s dvomi pracovnými postupmi tak rozhodovala hodom mincou — ktorý beh sa zaregistroval posledný, ten
verdikt dostala. 14.09.2026 prešiel NEX Inbox 1.5.0 do stavu Hotovo s padajúcim zostavením; jedna
z padajúcich skúšok strážila štítok „Duplicita (DB)", ktorý pri úpravách vzhľadu ticho vypadol.
Tá časť je opravená — brána číta všetky behy.

**Čo zostávalo.** Manažér sa o červenom CI dozvie až na bráne, teda vtedy, keď je verzia „hotová"
a prerába sa. Kto vidí červenú pri druhom commite, sa do toho stavu nedostane.

⚠️ Čo tieto stráže NEDOVOLIA zmeniť:

1. **Pravidlo je JEDNO a rozhodujú podľa neho obaja** — brána aj obrazovka. Dva kusy kódu, ktoré si
   samostatne vykladajú „čo je červená", sa raz rozídu a Manažér uvidí zelenú tam, kde brána vidí
   červenú. To je tá istá chyba ako pôvodná, len o poschodie vyššie.
2. **Červená, keď zlyhal HOCIKTORÝ dokončený beh — v OBOCH poradiach.** Pôvodná chyba bola hod
   mincou podľa poradia, takže stráž skúšajúca jedno poradie ju nechytí. Toto je presne to, čo
   tiket žiada menom.
3. **Obrazovka NEČAKÁ.** Brána si počká, kým beh vznikne a dobehne; obrazovka berie okamžitý obraz.
   Čakajúci prehľad by pri každom otvorení visel minúty.
4. **`unknown` sa nesmie tváriť ako zelená.** Nevedomosť nie je dobrá správa.
"""

from __future__ import annotations

import pytest

from backend.services import ci_status

# ── 1. jedno pravidlo, oba smery ──────────────────────────────────────────────


def _beh(meno: str, stav: str, zaver: str | None, cislo: int = 1) -> dict:
    return {"workflowName": meno, "status": stav, "conclusion": zaver, "databaseId": cislo}


class TestPravidloVerdiktu:
    def test_a_failed_run_makes_it_red_whichever_order_it_arrives_in(self):
        """⚠️ Jadro tiketu. Pôvodná chyba bola hod mincou podľa poradia — preto obe poradia."""
        zlyhal = _beh("CI", "completed", "failure", 34869173177)
        presiel = _beh("Release smoke gate", "completed", "success", 34869175048)

        for poradie in ([zlyhal, presiel], [presiel, zlyhal]):
            stav, detail = ci_status.verdikt(poradie)

            assert stav == "red", (poradie, detail)
            assert "34869173177" in detail, f"nepomenoval, ktorý beh zlyhal: {detail}"
            assert "CI" in detail

    def test_the_red_detail_names_the_workflow_not_just_the_number(self):
        """14.09.2026 brána ohlásila „CI zelené (beh 34869175048)" o behu ÚPLNE INÉHO postupu. Jediné
        slovo, na ktoré sa Manažér spoliehal, bolo to nesprávne."""
        stav, detail = ci_status.verdikt([_beh("Release smoke gate", "completed", "failure", 7)])

        assert stav == "red"
        assert "Release smoke gate" in detail, detail

    def test_all_passed_is_green(self):
        stav, detail = ci_status.verdikt(
            [_beh("CI", "completed", "success", 1), _beh("Smoke", "completed", "skipped", 2)]
        )

        assert stav == "green", detail

    def test_a_run_still_going_is_not_a_verdict(self):
        """Bežiaci beh nehovorí o kóde nič — ani dobre, ani zle."""
        stav, detail = ci_status.verdikt([_beh("CI", "completed", "success", 1), _beh("Smoke", "in_progress", None, 2)])

        assert stav == "unknown", detail
        assert "Smoke" in detail

    def test_no_runs_at_all_is_unknown(self):
        stav, _ = ci_status.verdikt([])

        assert stav == "unknown"

    def test_cancelled_alone_is_unknown_not_red(self):
        """⚠️ Zrušený beh nie je dôkaz o chybe. Brána, ktorá zhodí verziu preto, že niekto stlačil
        Zrušiť, je brána, ktorú sa ľudia naučia obchádzať."""
        stav, detail = ci_status.verdikt([_beh("CI", "completed", "cancelled", 1)])

        assert stav == "unknown", detail

    def test_cancelled_next_to_a_failure_is_still_red(self):
        """Opačný smer: zrušený beh nesmie zakryť ten, ktorý naozaj padol."""
        stav, _ = ci_status.verdikt([_beh("CI", "completed", "cancelled", 1), _beh("Smoke", "completed", "failure", 2)])

        assert stav == "red"


# ── 2. brána a obrazovka rozhodujú podľa TOHO ISTÉHO ──────────────────────────


class TestJednoPravidloDvajaCitatelia:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("znamka", [("red", "ZNAMKA CERVENA"), ("green", "ZNAMKA ZELENA")])
    async def test_the_gate_returns_what_the_shared_rule_says(self, monkeypatch, tmp_path, znamka):
        """⚠️ Toto je to, čo tiket naozaj rieši: verdikt brány MUSÍ vyjsť zo zdieľaného pravidla.

        Meria sa to správaním, nie prítomnosťou reťazca v zdroji. Prvá podoba tejto stráže hľadala
        v zdroji `ci_status.verdikt` — a mutácia, ktorá záver brány prepísala na natvrdo zelený,
        cez ňu prešla, lebo ten reťazec v zdroji zostal o kus vyššie. Stráž merala prítomnosť
        volania, nie to, či z neho odpoveď naozaj vychádza.

        ⚠️ A skúša sa OBOMA známkami. Prvá podoba dávala len červenú — lenže tá zaskočí skoršiu vetvu
        (brána sa pri červenej vracia hneď, ešte z čakacej slučky), takže mutácia natvrdo zeleného
        ZÁVERU cez ňu prešla druhýkrát. Jedna cesta cez funkciu nedokazuje druhú.
        """
        from backend.services import ci_status as modul
        from backend.services import orchestrator

        async def _krok(prikaz, _timeout):
            if "remote.origin.url" in prikaz:
                return 0, "https://github.com/rauschiccsk/nex-inbox.git"
            return 0, '[{"status": "completed", "conclusion": "success", "databaseId": 1, "workflowName": "CI"}]'

        monkeypatch.setattr(orchestrator, "_repo_head", lambda _root: "abc1234")
        monkeypatch.setattr(orchestrator, "_project_has_ci", lambda _root: True)
        monkeypatch.setattr(orchestrator, "_run_publish_step", _krok)
        # Pravidlo povie niečo, čo by brána sama nikdy nevymyslela — a musí to zaznieť na jej výstupe.
        monkeypatch.setattr(modul, "verdikt", lambda rows: znamka)

        stav, detail = await orchestrator._ci_status_for_head(tmp_path)

        assert (stav, detail) == znamka, (stav, detail)

    def test_both_ask_github_the_same_question(self):
        """Aj dopyt je jeden. Keby si obrazovka pýtala iné stĺpce, rozhodovala by z menšieho obrazu —
        napríklad bez mena postupu, ktoré je pri červenej to podstatné."""
        import inspect

        from backend.services import orchestrator

        zdroj = inspect.getsource(orchestrator._ci_status_for_head)

        assert "ci_status.prikaz_na_behy" in zdroj, "brána si skladá vlastný dopyt"


# ── 3. obrazovka nečaká ───────────────────────────────────────────────────────


@pytest.fixture()
def git_odpoveda(monkeypatch):
    """Projekt je repozitár s remote na GitHub. Skúšky nižšie sú o CI, nie o gite."""

    def _git(prikaz, cwd=None):
        if "rev-parse" in prikaz:
            return 0, "abc1234def5678\n"
        if "remote.origin.url" in prikaz:
            return 0, "https://github.com/rauschiccsk/nex-inbox.git\n"
        raise AssertionError(f"neočakávaný príkaz: {prikaz}")

    monkeypatch.setattr(ci_status, "_bez_cakania", _git)


class TestObrazovkaNecaka:
    @pytest.mark.asyncio
    async def test_the_snapshot_never_waits_for_a_run_to_appear(self, monkeypatch, tmp_path, git_odpoveda):
        """⚠️ Prehľad stavby sa obnovuje každých 25 s. Čakajúci dopyt by ho pri každom otvorení
        zavesil na minúty — a Manažér by si myslel, že zamrzol."""
        spane: list = []
        monkeypatch.setattr(ci_status.asyncio, "sleep", lambda s: spane.append(s))
        monkeypatch.setattr(ci_status, "_behy_z_githubu", _fake_behy([]))
        ci_status._zabudni_vsetko()

        vysledok = await ci_status.snapshot(tmp_path)

        assert vysledok.stav == "unknown"
        assert spane == [], "obrazovka čakala"

    @pytest.mark.asyncio
    async def test_the_snapshot_is_remembered_for_a_while(self, monkeypatch, tmp_path, git_odpoveda):
        """GitHub sa nemá pýtať pri každom obnovení prehľadu. Jedna odpoveď na minútu stačí."""
        volania: list = []

        async def raz(_root, _repo, _sha):
            volania.append(1)
            return [_beh("CI", "completed", "success", 5)], None

        monkeypatch.setattr(ci_status, "_behy_z_githubu", raz)
        ci_status._zabudni_vsetko()

        await ci_status.snapshot(tmp_path)
        await ci_status.snapshot(tmp_path)

        assert len(volania) == 1, f"pýtal sa GitHubu {len(volania)}×"

    @pytest.mark.asyncio
    async def test_the_memory_expires_so_a_fixed_build_stops_showing_red(self, monkeypatch, tmp_path, git_odpoveda):
        """⚠️ Druhý smer tej istej stráže. Bez neho by „pamätá si minútu" a „pamätá si navždy" vyzerali
        rovnako — a pamätanie navždy znamená, že červená zostane na obrazovke aj po tom, čo je opravená.
        Mutácia „zahoď platnosť" cez jednosmernú podobu prešla zelená (25.09.2026)."""
        volania: list = []

        async def pocitaj(_root, _repo, _sha):
            volania.append(1)
            return [_beh("CI", "completed", "success", 5)], None

        monkeypatch.setattr(ci_status, "_behy_z_githubu", pocitaj)
        ci_status._zabudni_vsetko()
        hodiny = [1000.0]
        monkeypatch.setattr(ci_status.time, "time", lambda: hodiny[0])

        await ci_status.snapshot(tmp_path)
        hodiny[0] += ci_status.PAMAT_SEKUND + 1
        await ci_status.snapshot(tmp_path)

        assert len(volania) == 2, f"po vypršaní platnosti sa GitHubu spýtal {len(volania)}×"

    @pytest.mark.asyncio
    async def test_an_unreachable_github_is_unknown_not_green(self, monkeypatch, tmp_path, git_odpoveda):
        """⚠️ Nevedomosť sa nesmie tváriť ako dobrá správa."""
        monkeypatch.setattr(ci_status, "_behy_z_githubu", _fake_behy(None, "GitHub neodpovedal"))
        ci_status._zabudni_vsetko()

        vysledok = await ci_status.snapshot(tmp_path)

        assert vysledok.stav == "unknown"
        assert "neodpoved" in vysledok.detail.lower(), vysledok.detail


def _fake_behy(rows, chyba: str | None = None):
    async def _f(_root, _repo, _sha):
        return rows, chyba

    return _f

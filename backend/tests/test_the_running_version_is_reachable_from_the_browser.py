"""Verzia bežiaceho backendu sa musí dať prečítať z prehliadača (ICCINT-128).

**Čo tomu predchádzalo.** Panel mal od 14.09.2026 pravidlo, ktoré pri rozdiele ukáže obe polovice.
Pravidlo bolo správne a aj tak panel klamal: 24.09.2026 hlásil v4.40.2, kým backend bežal na v4.40.7.
Číslo backendu sa k nemu totiž nemalo ako dostať — pýtal sa naň na ``/health``, a tú adresu si
odchytáva nginx frontendu a odpovedá zaň sám, bez verzie. Pravidlo dostávalo ``None`` a poctivo
padalo späť na číslo frontendu.

Poistka bez dosiahnuteľného údaja je poistka, ktorá nedrží — a pritom tvrdí, že drží. Preto sú
stráže nad cestou dve: tu, že ju backend naozaj vydáva, a vo frontende, že sa panel pýta na adresu,
ktorú nginx (a vo vývoji vite) posiela sem.
"""

from __future__ import annotations

from backend.config.settings import settings


def test_the_running_version_has_its_own_address(client) -> None:
    """⚠️ Jadro veci: bez tejto cesty je celé ICCINT-128 nefunkčné."""
    odpoved = client.get("/api/v1/version")

    assert odpoved.status_code == 200, "cestu na verziu nemá kto obslúžiť"
    assert odpoved.json()["version"] == settings.app_version


def test_it_lives_under_the_prefix_the_browser_can_reach(client) -> None:
    """Predpona ``/api/`` je presmerovaná na backend v prevádzke aj vo vývoji. Mimo nej by to vo
    vývoji ticho nefungovalo — tá istá pasca ešte raz."""
    assert client.get("/api/v1/version").status_code == 200


def test_it_answers_without_signing_in(client) -> None:
    """To isté číslo potrebuje aj prihlasovacia obrazovka — tam ešte nikto prihlásený nie je."""
    odpoved = client.get("/api/v1/version", headers={})

    assert odpoved.status_code == 200, "prihlasovacia obrazovka by verziu nedostala"


def test_it_carries_the_version_and_nothing_else(client) -> None:
    """Čo netreba, to sa nevydáva: sonda ``/health`` nesie stav databázy a sandboxu, tu stačí číslo."""
    assert set(client.get("/api/v1/version").json()) == {"version"}

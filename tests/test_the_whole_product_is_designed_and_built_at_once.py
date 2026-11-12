"""DEV-366 — the whole application is designed at the start and built in its first version, not split into pieces.

Director 12.11.2026, on the Vizuál of NEX Finance 0.1.0: „Úloho tejto aplikácie poskytovať finenčné informácie
o spoločností pre vrcholové vedenie. … dodávateľské faktúry su len jedným z analytický zdrojov informácie. … Podľa mňa
problém je vtom, že za posledné obdobie začíname rozbíjať projekt na 0.1.0, 0.2.0 a tým že niečo plánujeme pridať až
neskôr, čo je organickou súčasťou projektu, potom systém neráta s týmito informáciami.“ And: „My odzačiatku používame
systém waterflow, kde má byť navrhnutá celá aplikácia hneď pri začiatku … Ak staviteľ nevidí celý projekt potom nevie
to dobre navrhnúť. V každom prípade treba to spraviť tak, aby nerozbíjať to na tažko spojiteľné kúsky.“ On the wording
— the first version designs and builds the whole application, a part that depends on another system is built whole on
its own side, later versions are for later changes — „Súhlasím“.

The Projektant's charter taught the opposite: „Rozsah prvej verzie 0.1.0 — z nápadu najmenší celok … Zvyšok ostane
v zadaní ako ďalšie verzie.“ NEX Finance 0.1.0 became „časť Faktúry“: its overview a list of supplier invoices, the
cash desk, the bank and the customer invoices put off to 0.2.0–0.4.0, and neither Návrh nor Vizuál counted with them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import orchestrator
from tests.test_orchestrator_v2_verifikacia import _make_version

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _charter(name: str) -> str:
    return _flat((TEMPLATES / name).read_text(encoding="utf-8"))


@pytest.fixture
def version(db_session, monkeypatch, tmp_path):
    version, _ = _make_version(db_session, project_dial="plna")
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)
    return version


def test_no_template_teaches_the_smallest_whole():
    """A rule, not a list: no charter or brief of the cockpit tells an agent to cut a product to its smallest part."""
    teaching = [p.name for p in sorted(TEMPLATES.glob("*.md")) if "najmenší celok" in _flat(p.read_text("utf-8"))]
    assert teaching == []


def test_the_projektant_writes_the_brief_of_the_whole_product():
    text = _charter("poradca-ideas-charter.md")

    assert (
        "4. **Celý produkt v prvej verzii** — zadanie opíše celý produkt: jeho účel, prehľad, všetky organické časti "
        "(evidencie, obrazovky, rozhrania na iné systémy) a dátový model celku. Prvá verzia celú aplikáciu navrhne aj "
        "postaví. Časť, ktorá závisí od iného systému, postaví celú na svojej strane; druhú stranu rieši projekt toho "
        "systému. Produkt nerozbíjaj na verzie ani na ťažko spojiteľné kúsky — ďalšie verzie sú len na neskoršie "
        "zmeny." in text
    )
    assert "- **Čo má aplikácia vedieť** — celý produkt, očíslované, konkrétne, overiteľné;" in text
    assert "- **Mimo rozsahu** — len to, čo do produktu nepatrí; časť produktu sa do ďalšej verzie neodkladá;" in text
    assert "čo pôjde v ďalších verziách" not in text


def test_the_stavitel_designs_and_builds_the_whole(db_session, version):
    brief = _flat(orchestrator._priprava_directive(db_session, version.id))

    assert (
        "CELOK: Navrhuj pre celý produkt — Špecifikácia, dátový model aj obrazovky musia rátať so všetkým, čo zadanie "
        "opisuje (pri prvej verzii je to celá aplikácia). Čo zadanie menuje, postav v tejto verzii celé; nič z neho "
        "neodkladaj do „Mimo rozsahu“ bez rozhodnutia Manažéra a nerozbíjaj to na ťažko spojiteľné kúsky. Časť, ktorá "
        "závisí od iného systému, postav celú na strane aplikácie." in brief
    )


def test_the_auditor_takes_only_the_manazers_deferral_for_no_gap(db_session, version):
    brief = _flat(orchestrator._auditor_upfront_directive(db_session, version.id))

    assert "EXPLICITNE odložená mimo rozsah rozhodnutím Manažéra zapísaným v Špecifikácii" in brief
    assert "Časť zadania, ktorú Staviteľ odložil sám, JE medzera." in brief
    assert "EXPLICITNE odložená mimo rozsah s odôvodnením" not in brief

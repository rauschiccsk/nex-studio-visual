"""DEV-511 — what a new project needs from the internet (schemas, forms, manuals) the cockpit downloads itself into the
project; the Projektant only lists it with exact addresses, the Manažér downloads nothing.

25.11.2026, the brief of NEX DualBook in Nápady, point 63: „Staviteľ nemá prístup na internet, preto ich Manažér pred
začatím návrhu nahrá do dokumentácie projektu do priečinka docs/reference/“ — ten files (XSD schemas of VAT, forms,
the bank's integration manual), two of them without an exact address. The Projektant reads the internet (DEV-430)
but cannot save a file, the Staviteľ has no internet, and the cockpit had no way to bring a file from the internet into
a project. Director: „…mám ich vložiť ručne čo ja robiť nebudem.“ — on the proposal: „Súhlasím.“
"""

from __future__ import annotations

from pathlib import Path

from backend.services.poradca import handoff, references

GOOD = """\
docs/reference/dph/dph2025.xsd | https://ekr.financnasprava.sk/Formulare/XSD/dph2025.xsd | Schéma XSD priznania k DPH
docs/reference/banka/businessnet-connect-prirucka.pdf | https://www.unicreditbank.sk/a/b/Prirucka.pdf | Príručka banky
"""


def _brief(listing: str, extra: str = "") -> str:
    return (
        "Odpoveď.\n\n<zadanie-projektu>\nmeno: NEX DualBook\nslug: nex-dualbook\ndruh: poziadavka\n---\n"
        f"Celé zadanie projektu, ktoré má dosť textu.\n\n<podklady>\n{listing}</podklady>\n{extra}</zadanie-projektu>\n"
    )


# ── 1. the list in the brief ──────────────────────────────────────────────────────────────────────────────────────


def test_the_list_is_read_with_path_address_and_description() -> None:
    found, problems = references.of_brief(handoff.project_brief(_brief(GOOD)).brief)

    assert problems == []
    assert found == [
        references.Reference(
            "docs/reference/dph/dph2025.xsd",
            "https://ekr.financnasprava.sk/Formulare/XSD/dph2025.xsd",
            "Schéma XSD priznania k DPH",
        ),
        references.Reference(
            "docs/reference/banka/businessnet-connect-prirucka.pdf",
            "https://www.unicreditbank.sk/a/b/Prirucka.pdf",
            "Príručka banky",
        ),
    ]


def test_a_brief_without_the_list_has_nothing_to_fetch() -> None:
    assert references.of_brief("Celé zadanie bez podkladov.") == ([], [])


def test_the_list_stays_in_the_brief_for_the_staviteľ() -> None:
    assert "<podklady>" in handoff.project_brief(_brief(GOOD)).brief


def test_the_tag_named_in_a_sentence_is_no_list() -> None:
    brief = "Podklady vymenuj v bloku `<podklady>` podľa časti 6.\n\n<podklady>\n" + GOOD + "</podklady>\n"

    found, problems = references.of_brief(brief)

    assert problems == [] and [one.path for one in found] == [
        "docs/reference/dph/dph2025.xsd",
        "docs/reference/banka/businessnet-connect-prirucka.pdf",
    ]


def test_an_item_without_an_exact_address_is_named() -> None:
    listing = GOOD + "docs/reference/dph/kv-dph-poucenie.pdf | poučenie MF SR č. MF/020232/2022-731 | Poučenie KV\n"

    _found, problems = references.of_brief(handoff.project_brief(_brief(listing)).brief)

    assert problems == [
        "docs/reference/dph/kv-dph-poucenie.pdf: chýba presná adresa https:// súboru — nájdi ju na internete, "
        "alebo položku vynechaj a povedz prečo."
    ]


def test_every_kind_of_wrong_line_is_named() -> None:
    listing = (
        "docs/reference/a.pdf | http://example.sk/a.pdf | Nešifrovaná\n"
        "docs/other/b.pdf | https://example.sk/b.pdf | Mimo priečinka\n"
        "docs/reference/../c.pdf | https://example.sk/c.pdf | Von zo stromu\n"
        "docs/reference/d.exe | https://example.sk/d.exe | Program\n"
        "docs/reference/e.pdf | https://example.sk:8443/e.pdf | Iný port\n"
        "docs/reference/f.pdf | https://example.sk/f.pdf |\n"
        "docs/reference/g.pdf https://example.sk/g.pdf\n"
        "docs/reference/a.pdf | https://example.sk/h.pdf | Dvakrát tá istá cesta\n"
    )

    _found, problems = references.of_brief(f"Zadanie.\n<podklady>\n{listing}</podklady>")

    assert problems == [
        "docs/reference/a.pdf: chýba presná adresa https:// súboru — nájdi ju na internete, alebo položku vynechaj "
        "a povedz prečo.",
        "docs/other/b.pdf: cesta musí byť v priečinku docs/reference/ (písmená bez diakritiky, číslice, . _ -).",
        "docs/reference/../c.pdf: cesta musí byť v priečinku docs/reference/ (písmená bez diakritiky, číslice, . _ -).",
        "docs/reference/d.exe: kokpit sťahuje len dokumenty (pdf, doc, docx, xls, xlsx, odt, ods, zip, xsd, xml, "
        "txt, md, csv, json, html).",
        "docs/reference/e.pdf: adresa musí byť na štandardnom porte https (bez :port).",
        "docs/reference/f.pdf: chýba opis, čo súbor je.",
        "riadok „docs/reference/g.pdf https://example.sk/g.pdf“ nemá tvar „cesta | adresa | opis“.",
        "docs/reference/a.pdf: tá istá cesta je v zozname dvakrát.",
    ]


def test_the_answer_with_a_wrong_list_offers_nothing_and_says_why() -> None:
    listing = "docs/reference/kv.pdf | poučenie MF SR | Poučenie\n"

    said = handoff.withheld(_brief(listing))

    assert said == (
        "Kokpit neponúka zadanie, ktorého zoznam podkladov z internetu nie je úplný: docs/reference/kv.pdf: chýba "
        "presná adresa https:// súboru — nájdi ju na internete, alebo položku vynechaj a povedz prečo. "
        "Požiadaj o opravu zoznamu podkladov."
    )
    assert handoff.withheld(_brief(GOOD)) is None


def test_the_agent_is_told_what_to_correct() -> None:
    listing = "docs/reference/kv.pdf | poučenie MF SR | Poučenie\n"

    directive = references.rewrite_directive(references.problems_in_answer(_brief(listing)))

    assert directive.startswith("[Kontrola kokpitu] Zoznam podkladov z internetu v zadaní projektu (`<podklady>`)")
    assert "docs/reference/kv.pdf: chýba presná adresa https:// súboru" in directive
    assert "Kokpit ich pri založení projektu stiahne sám" in directive


CHARTER = Path(__file__).resolve().parents[2] / "templates" / "poradca-ideas-charter.md"


def test_the_projektant_is_taught_a_list_the_cockpit_takes_and_no_download_for_the_manažér() -> None:
    charter = CHARTER.read_text(encoding="utf-8")

    found, problems = references.of_brief(charter)

    assert problems == [] and len(found) == 1
    assert "Nikdy ich neukladaj Manažérovi na stiahnutie" in charter

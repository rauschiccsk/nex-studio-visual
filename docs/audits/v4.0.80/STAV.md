# Záverečný audit v4.0.79 — stav opráv

Audit: 82 agentov, 10 tried chýb, každý nález overený nezávislým skeptikom.
**70 navrhnutých → 53 potvrdených, 17 zamietnutých pri overení.**

Plný zoznam s dôkazmi: `closing-audit-findings.md` (číslovanie [1]–[53] je záväzné).

## Hotové

| skupina | opravené | commit |
|---|---|---|
| závažné | **10 z 10** | `5c5b010` |
| stredné | 13 z 28 | `0a564c8`, `48bcd79` |
| kozmetické | 1 z 15 | `0a564c8` |

Nasadené ako **v4.0.80**, CI zelené (behy 30380006910, 30381616881), kokpit
aj backend overené naživo.

## Zostáva — 15 stredných

Čísla odkazujú na `closing-audit-findings.md`.

- Ukladanie v Nastaveniach pri prázdnom formulári ticho nič neurobí (v knižnici `nex-shared`)
- `custom_development_enabled` — zaškrtávacie políčko „Vývoj na zákazku"; hodnota sa uloží a **nikto ju nečíta**
- `claude_cli_path` rozhoduje o hlásení zdravia, ale nie o tom, ktorý program sa spustí
- Overovací test pri zakladaní hlási PASS aj po neúspešnej kontrole zdravia
- Tabuľka používateľov v Nastaveniach ostane pri chybe prázdna a bez slova
- Číslovanie v charte Audítora nesedí s runbookom, ktorý sama vyhlasuje za záväzný
- Varovania z evidencie portov backend posiela, ale typ v rozhraní ich zahadzuje
- Zamietnutú požiadavku v Zásobníku nemožno vrátiť
- Runbook pre prvé UAT prikazuje ručný krok s nginx, ktorý skript už nerobí
- `docs/ARCHITECT_SETUP.md` opisuje cestu a pripojenie, ktoré neexistujú
- Políčko pri zakladaní sľubuje automatické nasadenie po každej zmene; CI šablóna nasadzovanie nemá
- Text políčka „Vývoj na zákazku" sľubuje odchýlku od jednotného dizajnu; nič to nečíta
- Neúspešná predbežná kontrola gitu natrvalo zablokuje „Uložiť Zadanie" bez cesty von
- Presun `accept()` pred vyhľadanie verzie spravil z ukončenia 4004 nekonečnú slučku pripájania
- Zmena nastavenia „kde býva zdrojový kód" vyrobí projekty bez chárt agentov

## Zostáva — 14 kozmetických

Mŕtve moduly, ktoré sa tvária ako živé (`knowledge_search.py`, `live_documents.py`,
`claude_subprocess.run_claude_stream`, `TaskPlanPanel`, `ComingSoonPage`, slovenský
kontrolór pravopisu vrátane 3,5 MB slovníka v nasadenej appke, `html2canvas`,
`templates/coordinator-settings.json`), `guardian_enabled` bez čitateľa, `module_id`
v typoch po zrušenom stĺpci, tlačidlo „+ Nový prístup" bez účinku pri chybe, brána
vydania, ktorá nevie spadnúť, popis nástenky bez fázy Vizuál, a zopár zastaraných
komentárov.

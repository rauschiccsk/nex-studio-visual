# v4.177.1

**Náhľad Vizuálu už neostane čierny, keď v projekte chýba súbor simulovaného servera.** Náhľad beží bez skutočného
servera — jeho odpovede simuluje MSW a ten potrebuje súbor `mockServiceWorker.js`. Doteraz ho náhľad bral z projektu,
kde ho agent mohol zmazať: orthodox-register 26.11.2026 ostal po Vizuáli celý čierny. Kokpit ho teraz náhľadu dáva sám
z nainštalovanej knižnice, vždy v tej istej verzii, akú náhľad používa. Náhľad, ktorý už beží, kokpit pri ďalšom pohľade
postaví nanovo na novom nastavení — nič netreba robiť.

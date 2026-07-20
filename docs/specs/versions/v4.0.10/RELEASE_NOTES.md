# v4.0.10 — NEX Studio Visual

## Keď sa blokujúca chyba vyriešila mimo projektu, koncové overenie sa dá spustiť bez zbytočnej opravy

Keď koncová Verifikácia niečo nájde, NEX Studio pošle AI partnerovi opravnú úlohu. Lenže občas je príčina **mimo samotného projektu** — v samotnom NEX Studiu (nástroji), nie v aplikácii. Vtedy niet čo v projekte opravovať, no opravná úloha aj tak čaká zmenu → točila sa dokola, dokonca tlačila AI, aby v projekte spravila zbytočnú zmenu, a k skutočnému overeniu sa nevrátila.

Od tejto verzie:

- **Nové tlačidlo „Znova overiť bez opravy"** — objaví sa práve vtedy, keď si v takejto opravnej slučke. Preskočí opravnú úlohu a rovno zopakuje koncové overenie (spustí aplikáciu a nezávislý Audítor ju posúdi). Ak prejde, verzia je pripravená na schválenie (Hotovo).
- Žiadne zbytočné úpravy testovaného projektu a žiadne zaseknutie, keď bola chyba mimo neho.

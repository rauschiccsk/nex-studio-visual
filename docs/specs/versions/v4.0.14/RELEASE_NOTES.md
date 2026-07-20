# v4.0.14 — NEX Studio Visual

## Nezávislý overovateľ dostáva istú informáciu, ktorú verziu testoval

Pri záverečnej Verifikácii sa stalo, že nezávislý overovateľ (Auditor) **nesprávne obvinil samotný nástroj** — usúdil, že skúška beží na starej verzii aplikácie spred opravy, hoci v skutočnosti bežala na aktuálnej. Chyba tak putovala k nám (do NEX Studia) namiesto tam, kde patrí.

Od tejto verzie:

- **Overovateľ dostáva „build-fakt"** — presnú informáciu (konkrétny commit), na ktorom skúška naozaj bežala, plus istotu, že nástroj vždy stavia a testuje **aktuálny kód**.
- Vďaka tomu **nemôže zlyhanie mylne pripísať „starému buildu"** ani ho falošne eskalovať ako chybu nástroja. Ak skúška padne, hľadá príčinu v aktuálnom kóde alebo v nestabilnom (náhodnom) teste — nie v neexistujúcej starej verzii.

Menej falošných obvinení, presnejšie nálezy.

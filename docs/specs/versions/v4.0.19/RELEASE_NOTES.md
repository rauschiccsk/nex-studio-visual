# v4.0.19 — NEX Studio Visual

## Token-spúšťané aplikácie sa už dajú spustiť z NEX Managera

Aplikácie, ktoré sa spúšťajú cez NEX Manager (token-launch, ako NEX Inbox), potrebujú na svojej strane špecifický „vstupný bod" — endpoint `GET /api/v1/launch`, cez ktorý ich NEX Manager reálne otvorí. Ukázalo sa, že AI to pri generovaní mohla vynechať (spravila len overenie tokenu na požiadavkách, nie ten vstupný bod), a appka sa potom z NEX Managera nedala spustiť — hlásila „Nenájdené".

Od tejto verzie:

- **Pokyny pre AI Agenta obsahujú presný kontrakt token-launch** — token-spúšťaná appka MUSÍ mať vstupný bod `GET /api/v1/launch?lt=…` (overí spúšťací token → prihlási používateľa → presmeruje do appky) a `GET /session`.
- **Nezávislý overovateľ to teraz kontroluje** — token-spúšťaná appka bez tohto vstupného bodu neprejde overením.

Vďaka tomu sa token-spúšťané aplikácie z dielne NEX Studia dajú spustiť z NEX Managera už na prvý pokus.

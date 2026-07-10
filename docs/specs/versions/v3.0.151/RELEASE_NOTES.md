## v3.0.151 — Keď niečo zlyhá: jasný stav a tlačidlo na obnovu

Keď stavba alebo krok zlyhal (alebo sa agent na niečo spýtal), cockpit doteraz hlásil len neurčité **„Čaká na súhlas"** a nebolo na čo kliknúť — jediná cesta vpred bola uhádnuť, že treba niečo napísať. Odteraz:

- **stav pomenuje, čo sa stalo** — napr. *„Systémová chyba"*, *„Agent zlyhal"*, *„Agent sa pýta"* — namiesto jedného všeobjímajúceho „Čaká na súhlas",
- ukáže **radu „čo ďalej"**, ktorú systém pripraví (predtým sa nezobrazovala),
- pridá **jasné tlačidlo**: pri chybe **„Skús znova"** (s nepovinným usmernením), pri otázke **„Odpovedať"**.

A po chybe už neponúkne mätúce „Schváliť špecifikáciu" — kým chybu nevyriešiš, ponúkne sa len obnova.

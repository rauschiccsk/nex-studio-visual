# v4.0.46 — NEX Studio Visual

## „Oprava po Verifikácii" dostane konkrétny dôvod zlyhania — koniec zacyklenia

Keď Verifikácia našla blokujúcu chybu a klikol si **„Nechaj to opraviť"**, AI Agent niekedy dostal len všeobecné *„appka sa nespustila"* — **bez konkrétneho technického dôvodu**, ktorý skúška po spustení naozaj našla. Preto mohol opraviť nesprávnu vec (napr. odladiť test namiesto skutočnej príčiny) a **tá istá chyba sa vrátila** — dokola.

Opravené:

- Fix-pokyn pre AI Agenta teraz **nesie presný technický dôvod zlyhania** (napríklad: stránka „Aktualizácie" v spustenej aplikácii nevracia očakávanú verziu). Agent tak od prvého pokusu smeruje na skutočnú príčinu, nie na jej vedľajší prejav.

Pre teba to znamená, že jeden klik na „Nechaj to opraviť" má oveľa väčšiu šancu vyriešiť problém napevno a nezacykliť sa.

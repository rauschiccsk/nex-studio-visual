# v4.0.23 — NEX Studio Visual

## Build verne dodá to, čo Manažér schválil vo Vizuáli

Druhý (a hlavný) krok opravy: doteraz sa výsledná appka mohla od schváleného Vizuálu **líšiť** — Programovanie stavalo nezávisle a schválený vizuál nič neviazalo. Manažér tak mohol schváliť jeden vzhľad a dostať iný.

Od tejto verzie je Vizuál **zmluva pre build**:

- **Pri schválení Vizuálu si systém zapamätá schválený stav obrazoviek** (konkrétny commit frontendu).
- **Programovanie ich len NAPOJÍ na reálne dáta — nesmie meniť vzhľad ani rozloženie.** Pokyny pre AI Agenta to teraz jasne vyžadujú: schválené obrazovky sa preberajú, neprerábajú.
- **Nezávislý Auditor vo Verifikácii overí zhodu** — porovná dodaný frontend oproti schválenému Vizuálu. Vypadnutý panel, zmenené rozloženie, iná paleta či prerobená obrazovka = **neprejde** (vráti sa na opravu).

Spolu s predchádzajúcou verziou (v4.0.22 — verný živý náhľad) to uzatvára princíp **„čo vidíš a schváliš vo Vizuáli, to aj dostaneš"**. Fáza Vizuál tým konečne dáva zmysel.

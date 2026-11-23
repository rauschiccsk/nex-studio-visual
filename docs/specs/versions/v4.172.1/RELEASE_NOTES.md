# v4.172.1

**Náhľad Vizuálu sa počas práce agenta neprestavuje.** Keď agent vo Vizuáli inštaluje knižnice projektu, kokpit
náhľad v tej chvíli neprestavuje — dve inštalácie do toho istého priečinka sa zrazili a náhľad sa nespustil. Náhľad
prestaví na knižnice, ktoré agent nechal, až keď agent dokončí svoju prácu. „Spustiť náhľad znova“ počas práce agenta
povie, že treba počkať.

**Na konci práce agenta náhľad naozaj beží.** Kým kokpit pošle správu, že návrh vizuálu je hotový, spustí aj náhľad,
ktorý medzitým spadol — odkaz už nevedie na stránku „404 page not found“.

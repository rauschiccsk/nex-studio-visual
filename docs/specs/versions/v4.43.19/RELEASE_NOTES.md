# v4.43.19

**Keď kontroly projektu zlyhajú, hláška na stránke UAT a PROD dovedie rovno k oprave.** Doteraz ponúkala len
„Otvoriť verziu“, ktoré viedlo späť k hotovej verzii. Teraz: keď je oprava už rozpracovaná, tlačidlo
„Otvoriť verziu …“ vedie k nej (druhú opravu neponúkne); keď Dedo pripravil zadanie opravy, „Otvoriť zadanie
od Deda“ otvorí projekt priamo pri ňom; inak „Spustiť rýchlu opravu“ otvorí projekt s otvoreným dialógom
rýchlej opravy.

**Rýchla oprava sa vždy pýta na druh práce.** Či opraví chybu v dodanom kóde (neúčtuje sa), alebo urobí zmenu
(účtuje sa), sa teraz volí aj vtedy, keď ju spúšťaš zo zadania od Deda na stránke projektu alebo z Dedovho návrhu
pri verzii — nielen v dialógu „Rýchla oprava“. Bez tejto voľby sa rýchla oprava nespustí, takže súpis dodaných
tokenov už nemusí druh práce dopĺňať dodatočne.

**Poradcovi môžeš do otázky vložiť snímku obrazovky.** Stačí ju do poľa otázky vložiť zo schránky (Ctrl+V
alebo Shift+Insert), pretiahnuť súbor myšou alebo ju vybrať tlačidlom s obrázkom vedľa poľa. Pred odoslaním
vidíš náhľad a snímku môžeš odobrať; k jednej otázke sa dá priložiť do 5 obrázkov (PNG, JPEG, WebP, GIF, každý
do 5 MB). Poradca si snímku pri odpovedi naozaj pozrie a v rozhovore ju uvidíš pri otázke — kliknutím celú.
Snímky ostávajú len pri rozhovore (nikdy v projekte) a vymazaním rozhovoru zmiznú. Zároveň sa opravilo, že
obrazovka kokpitu odmietala každý súbor väčší než 1 MB — aj súbor priložený agentovi.

**Previerka projektu vo Verifikácii beží tam, kam patrí, a nezaplní server.** Skript previerky projektu
(release_smoke_test.sh) teraz beží v priečinku projektu, jeho dočasné súbory ležia tam, kde ich vidí aj Docker,
a stráži ho strážca disku: keď previerka zaberie na disku servera viac než 30 GB alebo na ňom ostane menej než
20 GB voľných, zastaví sa a Verifikácia povie prečo. Predtým skúšky jedného projektu dokázali zaplniť celý disk.

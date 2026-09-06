# v4.12.0 — Testy v CI konečne vidia súbory, ktoré v projekte naozaj sú

Kontroly projektu nex-productcatalogs padali celý deň na hlášku, ktorá nedávala
zmysel: *„databáza catalogs_test neexistuje"* — hoci súbor, ktorý ju zakladá,
v projekte pokojne ležal a lokálne fungoval.

## Čo za tým bolo

Kontroly bežia v samostatnom kontajneri, ale príkazy vykonávajú cez hlavný
systém servera. Keď si teda projekt povie „vezmi tento priečinok z repozitára",
hľadá sa **na serveri** — a tam ten priečinok nebol, lebo kópia projektu žila
len vnútri toho kontajnera.

A najhoršie na tom: Docker v takom prípade **nič nepovie**. Ticho vyrobí prázdny
priečinok a podstrčí ho. Databáza tak dostane prázdno namiesto zakladacieho
skriptu, nikto sa nič nedozvie, a chyba sa prejaví o tri kroky neskôr ako
nezmyselné hlásenie o chýbajúcej databáze.

Trvalo to deň. Prezradil to prázdny priečinok na disku servera, vyrobený presne
v čase behu kontrol.

## Čo sa mení

Kontrolný kontajner má teraz svoj pracovný priečinok **na tej istej ceste ako
server** (`/opt/ci-work/<projekt>`). Obe strany sa tak pozerajú na tie isté
súbory a čo je v projekte, to kontroly aj vidia.

## Koho sa to týka

Každého projektu založeného z kokpitu — chyba bola v zakladaní, takže ju zdedil
každý. Staršie projekty, ktorých kontroly bežia priamo na serveri, sa jej
nikdy netýkali.

Nové projekty už dostanú kontroly správne rovno pri založení.

# v4.8.2 — Poznámky k vydaniu konečne hovoria zákazníkovi, čo dostal

Na karte **Aktualizácie** číta účtovníčka jediný text z projektu, ktorý sa jej dostane
pred oči celý. Práve tam sa šesť kôl po sebe objavovalo niečo, čo jej nepovie nič —
a projekt nex-productcatalogs sa na tom šesťkrát zasekol pred vydaním.

## Dve chyby, ktoré sa dopĺňali

**Prvá.** Do zákazníckeho textu padal aj náš vnútorný bod *„Opravy toho, čo previerka
pred vydaním našla"*. Vnútorné kolá opráv sa tam dostávať nemajú a v kóde na to
poistka **bola** — lenže hľadala epiku pod nesprávnym názvom. Kedysi sa volala
v jednotnom čísle, potom sa premenovala na množné a poistka za tým nešla. Nefungovala
odvtedy ani raz.

Horšie: **stráž na to existovala tiež**, dokonca dve. Jedna si test postavila z tej
istej nesprávnej hodnoty, takže overovala samú seba. Druhá porovnávala názov **úlohy**
namiesto názvu **epiky** — svietila zeleno celý čas a tvrdila, že poistka drží.

Názov teraz existuje **jediný raz** a obe strany si ho berú odtiaľ. Testy idú cez
skutočné zakladanie epiky a porovnávajú celý zoznam bodov, nie jeden reťazec.

**Druhá.** Pri rýchlej oprave zakladá zoznam prác stroj, takže tam nebola žiadna ľudská
veta — a text pre zákazníka vyšiel ako *„Rýchla oprava — Rýchla oprava"*.

AI Agent pritom poriadnu vetu vedel napísať a písal ju: priamo do súboru s poznámkami.
Ten súbor však vlastní a generuje NEX Studio, takže mu ho po každom kole prepísalo späť.
Agent nemal ako vyhrať.

## Čo sa mení

Pri rýchlej oprave teraz agent odovzdá **jednu vetu o tom, čo zákazník má** — a NEX Studio
si ju uloží k práci, nie do súboru. Vďaka tomu ju **žiadne ďalšie generovanie neprepíše**:
text býva v údajoch, odkiaľ sa poznámky skladajú.

Agent má v zadaní napísané aj to, čo do tej vety **nepatrí** — náš postup, interné kódy,
názvy súborov, počty kôl opráv. A výslovne, že do súboru s poznámkami písať nemá.

## Čo sa nemení

Poznámky k vydaniu naďalej vlastní NEX Studio a skladá si ich samo. To bol vždy správny
princíp — chýbalo len miesto, kam agent tú jednu ľudskú vetu odloží.

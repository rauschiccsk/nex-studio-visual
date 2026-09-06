# v4.13.1 — Nový projekt už nedostane pomôcku, ktorá si vyrába prázdne nastavenia

Každý nový projekt dostáva od NEX Studia pomocný skript, ktorý pred kontrolami
zostaví nastavenia zo vzoru. Ten skript prepisoval riadky doslova — **aj tie,
ktoré vo vzore zostali nevyplnené.**

## Prečo to vadí

Nevyplnená položka vo vzore znamená „toto doplň". Keď sa prepíše doslova, vznikne
z nej **prázdna hodnota** — a to sú dve rôzne veci. Nenastavená položka nechá
platiť predvolenú hodnotu aplikácie; prázdna sa **číta a vyhodnocuje**, a pri
položke, ktorá má byť zoznamom, na tom aplikácia spadne ešte pred prvým testom.

Na projekte NEX ProductCatalogs to zhodilo kontroly tak, že sa vôbec nespustili.

## Čo sa mení

Nevyplnené položky sa do nastavení **už nezapisujú**. Komentáre a prázdne riadky
prechádzajú nedotknuté a tri položky, ktoré skript pre kontroly zámerne prepisuje
(prístup k databáze a heslá), sa nemenia.

## Prečo je to zaujímavé

Toto je **tretie miesto**, kde sme to isté pravidlo museli napísať — po skúške
po spustení a po nasadzovaní do testovacieho prostredia. Zakaždým sa opravilo
len to miesto, kde to práve horelo.

Teraz je opravená aj šablóna, čiže miesto, odkiaľ to nové projekty dedia.

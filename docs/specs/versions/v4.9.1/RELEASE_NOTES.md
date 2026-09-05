# v4.9.1 — Nasadenie do UAT sa už nezasekne na prázdnej hodnote

Prvé nasadenie NEX ProductCatalogs 0.1.2 pre zákazníka ANDROS s.r.o. zlyhalo. Databáza
aj obrazovky nabehli, ale samotná aplikácia sa nespustila a nasadenie hlásilo len toľko,
že „backend nebeží".

## Čo za tým bolo

Každý projekt má vzorový súbor s nastaveniami, kde niektoré položky zostávajú prázdne —
znamená to **„toto doplň"**. Nasadenie do UAT ich však prepisovalo do ostrých nastavení
tak, ako boli, teda **ako prázdnu hodnotu**.

A to sú dve úplne odlišné veci. Nevyplnená položka nechá aplikáciu použiť vlastnú
predvolenú hodnotu. Prázdna hodnota sa **číta a vyhodnocuje** — a pri položke, ktorá má
byť zoznamom, na tom aplikácia spadne skôr, než vôbec nabehne.

V tom vzorovom súbore bolo takých prázdnych položiek dvadsaťpäť.

## Čo sa mení

Nevyplnené položky zo vzoru sa do nastavení **už nezapisujú** — nechajú sa nenastavené,
takže sa uplatní predvolená hodnota aplikácie.

Zámerne prázdne hodnoty sa nemenia. Napríklad kľúč pre spúšťanie z NEX Managera zostáva
prázdny vtedy, keď párový Manager neexistuje — a to je správne, spúšťanie je vtedy
jednoducho vypnuté. Máme na to test, aby sa táto oprava nedala omylom rozšíriť aj naň.

## Prečo to stojí za zmienku

**Túto chybu sme už raz opravili** — pri skúške po spustení, a v poznámke pri nej je
popísaný presne ten istý pád. Opravilo sa vtedy len jedno z dvoch miest, ktoré to
potrebovali, a druhé sa ozvalo o štyri týždne neskôr.

Teraz to pravidlo existuje **na jednom mieste** a obe cesty si ho berú odtiaľ, takže
tretia cesta ho nemôže obísť.

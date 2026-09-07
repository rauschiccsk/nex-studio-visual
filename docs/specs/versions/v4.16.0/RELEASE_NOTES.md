# v4.16.0 — Tlačidlo „Spustiť" v UAT konečne niekoho prihlási

Tlačidlo existovalo od jari a **nikdy nemohlo fungovať**. Nie preto, že by bolo
nedorobené — preto, že razilo vstupenku na meno `uat-test`, ktoré neexistuje.

## Čo sa dialo

Aplikácia po vstupe žiada svoj NEX Manager, aby potvrdil, kto je prihlásený.
Na otázku „kto je uat-test?" odpovedal, že takého človeka nepozná — a sedenie
skončilo. Zakaždým, u každého zákazníka.

Nebolo to prehliadnutie. V kóde stála poznámka, ktorá to rozhodnutie obhajovala:
nechcelo sa raziť na meno skutočného človeka, aby to nebola impersonácia.

## Prečo bol ten úmysel založený na nedorozumení

Impersonácia je raziť vstupenku na **cudzie** meno. Toto bol iný prípad:
prihlásený človek klikne vlastnou rukou vo vlastnom sedení a vstupenka má znieť
**na neho**. To nie je predstieranie cudzej totožnosti, ale overenie vlastnej.

Vyhýbanie sa impersonácii tu neochránilo nikoho — len vyrobilo tlačidlo, ktoré
vyzeralo funkčne a nebolo.

## Čo sa mení

Vstupenka znie na **teba** — na prihlasovacie meno, pod ktorým si v NEX Studiu.
Ostáva krátkodobá a jednorazová ako doteraz.

A keď NEX Manager tej aplikácie tvoje meno nepozná, dozvieš sa to **skôr, než sa
niečo pokazí**: NEX Studio sa ho spýta vopred a napíše ti to po slovensky, aj čo
s tým. Doteraz si videl „Prihlásenie skončilo" — hlášku z aplikácie, ktorá
ukazovala nesprávnym smerom.

Keď sa Manager spýtať nedá (nie je spárovaný, neodpovedá), spustenie sa nezastaví.
Nevedieť nie je to isté ako vedieť, že meno je neznáme.

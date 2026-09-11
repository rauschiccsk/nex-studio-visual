# v4.37.0 — sedem dokumentov, ktoré sa nevedeli zaindexovať nikdy

Po tom, čo sa vyhľadávanie v dokumentácii začalo dorovnávať samo, dobehlo na 198 z 205 dokumentov
a zastavilo sa. Posledných sedem zlyhávalo pri každom pokuse — vždy tie isté, vždy rovnako.

## Prečo

Dlhý text sa pred vyhľadávaním krája na menšie kusy; nastavenie hovorí, aké veľké majú byť.
Lenže to nastavenie **nebolo strop, len odporúčanie**: text sa krájal podľa nadpisov a odsekov,
a keď bol jeden odsek dlhý, nemal kde puknúť — prijal sa celý. Vznikali tak kusy šesťkrát väčšie,
než malo byť, a tie už vyhľadávanie odmietlo spracovať.

Netýkalo sa to náhodných súborov. Týkalo sa to každého dokumentu, ktorý má niekde hustý zoznam bez
prázdnych riadkov alebo veľkú tabuľku — a taký dokument sa **nezaindexoval nikdy**. Nebolo to
zdržanie, bola to trvalá diera.

## Čo je odteraz

Kusy sa krájajú tak, aby nastavenú veľkosť naozaj dodržali — na hranici slova, nech sa slová
netrhajú. Overené na tých siedmich súboroch: každý z nich teraz prejde celý.

Bežných dokumentov sa to nedotkne — nekrájajú sa na drobno, len prestali vznikať tie prehnane veľké.

Medzi dokumentmi, ktoré sa konečne zaindexovali, je aj špecifikácia **NEX Inboxu** — projektu, ktorý
ide na rad ako ďalší.

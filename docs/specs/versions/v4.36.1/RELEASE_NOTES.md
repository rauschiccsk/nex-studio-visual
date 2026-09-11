# v4.36.1 — poistka, aby dorovnávanie nikdy nevyprázdnilo vyhľadávanie

Verzia 4.36.0 priniesla, že sa vyhľadávanie v dokumentácii dorovnáva samo. Hneď v prvý deň sa ukázalo,
čo tomu chýbalo.

## Čo sa stalo

Pri spustení skúšok na serveri sa dorovnávač pozrel na **prázdny priečinok** (skúšky si Znalostnú bázu
zámerne nahrádzajú dočasnou). Usúdil z toho, že všetky dokumenty boli zmazané — a vymazal ich
z vyhľadávania. Zo 3718 zaznamenaných kúskov zostalo 293.

Nič sa nestratilo: dokumenty sú na disku a vyhľadávanie sa z nich prestavalo samo. Ale stať sa to
nemalo.

## Čo sa zmenilo

**Dorovnávač už nesmie vymazať väčšinu toho, čo pozná.** Keď mu zrazu „chýba" viac než pätina
dokumentov, nevyhodnotí to ako „boli zmazané", ale ako **„niečo je zle s priečinkom"** — nezmaže nič
a nahlási to.

Dopĺňanie chýbajúcich dokumentov obmedzené nie je. Rozdiel je zámerný: pridať dokument navyše sa dá
kedykoľvek vziať späť, vymazaný sa vracia ťažšie.

Bežné upratovanie funguje ďalej — keď naozaj zmažeš pár dokumentov, vyhľadávanie ich prestane ponúkať.

## Prečo to píšeme aj sem

Pravidlo *„keď to neviem, nebudem tvrdiť, že je všetko v poriadku"* sme v tejto časti mali od začiatku —
ale platilo len na to, **čo appka hovorí**. Nevzťahovalo sa na to, **čo appka robí**. Teraz sa vzťahuje
na oboje.

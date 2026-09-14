# v4.38.4 — väzba nájde poistku aj v staršej deklarácii

Poistky, ktoré boli vymenované ešte pred zavedením kľúčov, sa rozpoznávajú podľa svojho znenia.
Väzba k nim pridáva kľúč — a tým prestala sedieť. Odteraz sa nájdu **podľa kľúča aj podľa znenia**,
čo ktorá strana použila.

## Prečo

Stavba mala všetkých štrnásť poistiek zviazaných so skúškami a brána z nich videla nula. Jedna strana
hovorila kľúčom, druhá vetou — a hoci obe mysleli to isté, nestretli sa.

## Čo to znamená pre teba

Staršie projekty, ktoré kľúče ešte nemajú, nemusia nič prepisovať. Väzba k nim dosadne cez znenie a
kľúč sa doplní vtedy, keď sa deklarácia najbližšie píše.

A poistka, ktorú nikto nedeklaroval, sa týmto **nedá prepašovať**: väzba sa prijme len k tomu, čo
v deklarácii naozaj je.

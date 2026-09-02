# v4.5.0 — čo sa dohodne na obrazovkách, dostane sa do dokumentov

Vizuál je najlepšie miesto, kde sa dá dopovedať to, čo pri návrhu ušlo. Vidíš
appku, klikáš po nej a hovoríš „toto zoraď inak, sem pridaj stĺpec". Agent to
poslušne dorobí — a doteraz to zostalo **iba na tých obrazovkách**.

Schválenie Vizuálu zlisovalo obrazovky do jedného commitu a do Špecifikácie ani
do Návrhu nenapísalo ani slovo. Stavba tak od toho okamihu niesla dve pravdy,
ktoré si mohli protirečiť.

## Prečo to bolo horšie, než to znie

**Overenie sa odvodzuje zo Špecifikácie.** Čo žilo len na obrazovkách, sa pri
vydaní nikdy neskontrolovalo. Najnovšie a najmenej premyslené doplnky boli teda
tie **najmenej** preverené — presne naopak, než má byť.

**Ďalšia verzia číta Špecifikáciu ako jedinú pravdu.** Tie doplnky by sa buď
navrhli druhýkrát a inak, alebo by ticho vypadli.

**Plán úloh sa staval z „teplej" session** — z rozhovoru, ktorý sa postupne
skracuje a nakoniec zmizne.

## Čo sa mení

Keď schváliš Vizuál, agent dostane ešte jeden krok: prejde schválené obrazovky
a **dopíše ich do Špecifikácie a Návrhu**. Na obrazovky nesiaha, tie sú
schválené. Dokumenty sa podpíšu **tým istým commitom** ako obrazovky — jedno
rozhodnutie, jeden podpis.

### Keď dokument mlčal — doplní sa ticho

Väčšina doplnkov nie je spor. Špecifikácia o zoradení zoznamu nepovedala nič,
na obrazovke sa zoradilo podľa sumy — nie je čo riešiť, zapíše sa to a ide sa
ďalej. Karta by tu bola len otravovanie.

### Keď si dokument a obrazovka protirečia — pýtame sa teba

Špecifikácia hovorí „podľa dátumu", obrazovka robí „podľa sumy". To sú **dve
tvoje rozhodnutia z rôznych chvíľ** a agent nemá právo vybrať, ktoré platí.

Stavba sa zastaví na Vizuáli a dostaneš kartu, ktorá **doslova ukáže obe
strany** — čo píše dokument aj čo robí obrazovka. Nie kategóriu problému, ale
samotný spor. Vyberieš a ide sa ďalej.

### Doplnky ešte raz posúdi Audítor — ale len tie doplnky

Ak sa dokumenty naozaj zmenili, predbežná previerka prebehne znovu — **zúžená
presne na to, čo pribudlo**. Nie celá Špecifikácia od začiatku; to už raz
posúdená bola. Ak sa nezmenilo nič, previerka sa nespúšťa vôbec.

## Rýchla dráha zostáva rýchla

Oprava chyby cez rýchlu dráhu Vizuálom neprechádza a nič sa jej nedopisuje —
inak by prestala byť rýchla. Má však novú povinnosť: keď agent počas opravy
zistí, že sa dotkol **správania popísaného v dokumentácii**, musí to nahlásiť
a nechať rozhodnutie na tebe. Ticho to prejsť nesmie.

## Keď dopísanie zlyhá

Nedostupný agent stavbu **nezastaví**. Schválenie prejde a dostaneš správu, že
sa dopísanie nepodarilo. Chýbajúci odstavec, o ktorom vieš, je menšie zlo než
kokpit, cez ktorý sa nedá prejsť.

---

*Prečo stredná číslica: pribúda karta, akú si doteraz nevidel, schválenie
Vizuálu odteraz robí viac než predtým a môže spustiť druhú previerku. To nie je
oprava chyby — je to zmena správania, ktorú treba poznať.*

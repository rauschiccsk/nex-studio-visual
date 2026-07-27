# v4.0.60 — NEX Studio Visual

## Z Metrík sú Náklady

Obrazovka doteraz odpovedala na otázku „sme rýchlejší a lacnejší ako človek?" — štyri veľké čísla o tom, koľkonásobne a koľko sa ušetrilo. Odteraz odpovedá na otázku, ktorá má pre riadenie zmysel: **čo to stálo.**

Náklady vidíš v troch úrovniach — **po fázach, po verziách a za celý projekt**. V každej sú vedľa seba dve sumy: koľko stál výpočet AI a koľko by stála tá istá práca v ľudských hodinách.

## Externé náklady — čo appka nevidí, teraz zadáš ručne

Kokpit meria len prácu, ktorá prejde cez neho. Čokoľvek spravené mimo — v termináli, priamo v editore — v nákladoch projektu chýbalo, akoby to nič nestálo.

Pribudla časť **Externé náklady**, kam taký výdaj zapíšeš: dátum, popis, model a počet tokenov. Ocení sa rovnakým cenníkom a rovnakým koeficientom ako všetko ostatné. Voliteľne ho priradíš ku konkrétnej verzii, inak sa počíta projektu ako celku.

**Merané a ručne zadané sa nikdy nezlejú do jedného čísla.** Každý súčet ukazuje rozpad „z toho merané / z toho ručne zadané" — pri peniazoch, hodinách, ťahoch aj tokenoch. Meranému vieš veriť, ručne zadané je tvoj odhad, a obrazovka ti tie dve veci nikdy nezamení.

## Jeden koeficient namiesto piatich

Prepočet tokenov na ľudský čas bol nastavený zvlášť pre každú fázu — päť políčok, do ktorých patrí to isté číslo. Stačilo vyplniť jedno a druhé nie, a výsledky sa rozišli: dva kokpity tak tvrdili presný opak o tej istej práci.

Teraz je to **jedno políčko pre všetko**, a je napísané priamo v hlavičke stĺpca, aby bolo vidieť, na čom výsledok stojí. Hodinové sadzby zostávajú po fázach — tie sa naozaj líšia.

## Ceny v eurách

Ceny modelov boli označené v dolároch, mzdy v eurách, a rozdiel sa vypisoval s eurom. Všetko je zjednotené na eurá.

Zároveň platí, čo obrazovka aj napíše: **cena AI je hodnota spotrebovaného výpočtu podľa cenníka, nie minutá hotovosť** — platíme paušál.

## Drobnosti

Stĺpec „opravy" zmizol. Nepočítal opravy, ale koľkokrát bola AI vôbec oslovená — čistá práca tak vyzerala ako samá oprava. Nahradil ho zrozumiteľný počet ťahov.

Grafy sme odstránili. Neznámu hodnotu kreslili ako nulový stĺpec, čiže tvrdili, že tam nebolo nič — pritom sme len nevedeli.

Zmazanie verzie už nezmaže ručne zadané náklady; záznam sa zachová a preklopí na úroveň projektu.

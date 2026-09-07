# v4.20.0 — Zadanie sa už nedá prepísať bez toho, aby si to videl

Keď si pri zakladaní verzie uložil Zadanie, kokpit ho zapísal do súboru so
špecifikáciou — **a čo tam bolo predtým, zahodil**. Bez otázky, bez varovania.

## Ako to vyzeralo

Vo formulári si videl **prázdne pole**. Nemal si ako vedieť, že píšeš cez súbor,
v ktorom je sedemdesiat riadkov.

07. septembra tak zmizla celá zákaznícka špecifikácia pre v0.2.0 — rozhodnutie
o nočnom sťahovaní, poistka „pri zlyhaní platí posledný dobrý súbor", zákaz dostať
cenník do histórie projektu, upozornenie že odkaz na cenník je tajomstvo. Agent
namiesto nich dostal jednu vetu.

## Čo sa mení

**Kokpit ti teraz ukáže, čo v tom súbore je**, a nechá rozhodnúť teba. Priamo vo
formulári uvidíš celý existujúci text a tlačidlo, ktorým si ho prevezmeš do poľa —
môžeš ho doplniť a uložiť, alebo vedome nahradiť.

Ticho sa neprepíše nič. Uloženie toho istého textu druhýkrát ostáva bez následkov,
aby sa z ochrany nestala otrava, ktorú sa ľudia naučia obchádzať.

## Prečo to nebola drobnosť

V kóde stál napísaný predpoklad, že v tom priečinku ešte nič nie je. Predpoklad bol
väčšinou pravdivý — a nikdy sa neoveroval. Presne v tom je rozdiel medzi „zvyčajne
to vyjde" a „nemôže sa to pokaziť".

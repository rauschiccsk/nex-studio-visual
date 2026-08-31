# v4.4.0 — ťah sa končí, keď je práca hotová alebo zaseknutá, nie keď dobehli hodiny

## Čo bolo zle

Každý ťah agenta mal **pevný strop** — pri programovaní 40 minút. Rovnaký pre
jednoriadkovú opravu aj pre tridsať skúšok odvodených zo špecifikácie.

Dôsledok bol horší, než sa na prvý pohľad zdá. Veľká, ale poctivá úloha sa do
stropu nezmestila — a tak sa **práca prispôsobovala nástroju**: rozdelila sa na
dávky, aby sa do neho vošla. Cenu za to platil Manažér, ktorý musel štyrikrát
kliknúť a štyri razy čakať na jednu súvislú vec.

Manažér to odmietol:

> Ak sa testy nezmestia, potom to netreba rozbíjať, ale zvýšiť časový limit.
> Toto pre mňa nie je dlhodobé a nie je akceptovateľné riešenie.

Mal pravdu. Rozdeľovanie bolo obchádzka, nie riešenie.

## Čo sa zmenilo

**Hodiny odteraz merajú ticho, nie veľkosť úlohy.**

Zaseknutý agent prestane hovoriť; pracujúci nie. Rozpočet sa preto míňa na
**mlčanie** — kým agent posiela, čo robí, hodiny sa nekrátia. Veľká úloha už
neprepadne za to, že je veľká.

Pevný strop zostáva, ale len ako **poistka proti nekonečnému behu** — je
niekoľkonásobne vyšší a nemá tvarovať prácu.

## A hlásenie povie, čo sa naozaj stalo

Doteraz zaznelo iba *„vypršal časový limit"*, čo o príčine nehovorí nič. Teraz
sa rozlišuje:

- **agent mlčal N sekúnd** — zasekol sa; treba sa pozrieť, na čom
- **prekročený tvrdý strop** — beží dokola; to je iná situácia a iná odpoveď

Sú to dve rôzne veci s dvoma rôznymi riešeniami a doteraz sa nedali odlíšiť.

## Čo to znamená v praxi

Zadanie sa už neplánuje podľa limitu. Agent dostane **celok** a limit sa
prispôsobí — presne naopak, než to bolo doteraz.

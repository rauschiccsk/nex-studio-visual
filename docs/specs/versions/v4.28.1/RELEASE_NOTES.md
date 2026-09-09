# v4.28.1 — vyhradený blok portov konečne patrí tomu, komu je vyhradený

Prvé prevzatie NEX Managera skončilo hláškou:

> Port 10210 (Backend) patrí do bloku 10210–10219, ktorý je pridelený inému systému —
> **nex-manager**. Vyber port z bloku tohto projektu.

Ten „iný systém" bol ten istý projekt.

## Prečo

Evidencia portov dostávala len číslo portu a identifikátor *upravovaného* projektu — pri
zakladaní žiadny. Nemala teda ako vedieť, **kto sa pýta**, a vyhradený blok odmietla aj
vlastníkovi.

Netýkalo sa to len prevzatia: rovnako by dopadlo bežné založenie `nex-payables`, ktorého
blok 10220–10229 je v evidencii tiež. Prevzatie to len odhalilo, lebo pri ňom sa porty
čítajú z disku a trafia vyhradený blok vždy.

## Ako je to teraz

Kontrola vie, ktorý projekt sa pýta, a blok napísaný na jeho meno mu pustí. Cudzí blok
zostáva zatvorený — pomýliť sa opačným smerom by znamenalo, že si dvaja sadnú na tie isté
porty a príde sa na to až tým, že jednému prestane appka bežať.

Vlastníci sú v evidencii voľný text a nie sú jednotní — `nex-manager`, `nex-payables`, ale
aj `NEX Inbox`, `NEX Automat`, `icc-website (isnex.ai)`. Porovnáva sa preto zhovievavo, bez
medzier, pomlčiek a veľkých písmen, proti názvu **aj** skratke projektu.

## Poznámka k stráži

Prvá verzia stráže skúšala len porovnávanie mien vedľa opravy. Keď som pri overovaní vypol
tú vetvu, ktorá naozaj padala, **zostala zelená** — merala niečo iné než to, čo bolo
pokazené. Prepísaná je tak, aby sa pýtala priamo evidencie na verdikt, ktorý dostane
zakladanie projektu.

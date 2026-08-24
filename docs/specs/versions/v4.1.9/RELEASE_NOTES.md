# v4.1.9 — nový projekt sa už nerodí s tromi zrušenými rolami

## Čo bolo zle

Zakladanie projektu vytváralo tri role — Designer, Implementer, Auditor. Tie sú
zrušené od 23. augusta; dnes existujú dve: **AI Agent** a **Auditor**.

Na obrazovke to nebolo vidieť, lebo NEX Studio hneď po založení tri staré
priečinky zmazalo a napísalo dva správne. Projekt teda dopadol dobre — ale cesta
k nemu vyrábala tri sady pravidiel, dva zbytočné stavové súbory a dva prázdne
priečinky denníkov, ktoré vzápätí zmizli. A pri založení mimo NEX Studia (tak
vznikol NEX Automat) tam tie mŕtve role **zostali**.

## Čo sa zmenilo

**Pravidlá agenta píše jedno miesto — NEX Studio.** Zakladací skript ich prestal
písať úplne. Dovtedy ich mal vo vlastných šablónach, ktoré nikto neudržiaval;
dve kópie pravidiel, z ktorých jedna je mŕtva, sú presne ten druh nesúladu, čo
sa raz za čas prejaví ako záhadná chyba. Zmazaných je takmer 2 000 riadkov
pravidiel pre role, ktoré neexistujú.

**Stavové súbory a denníky** sa zakladajú pre dve role namiesto troch.

**Našla sa pritom aj tichá chyba:** zoznam súborov, ktoré sa nemajú ukladať do
histórie, obsahoval oba staré názvy, ale **nie ten nový**. Pracovný stavový
súbor AI Agenta by sa tak pri prvom uložení dostal do histórie projektu.
Opravené; staré názvy tam zostali kvôli projektom, ktoré ich ešte majú.

**Doménový variant** (osobitné pravidlá pre účtovné či mzdové projekty) sa už
nepripája nikam — pripájal sa do pravidiel Designera, teda do súboru, ktorý sa
o krok neskôr mazal, a NEX Studio navyše vždy posiela iba všeobecný variant.
Súbory s tými pravidlami zostávajú zachované. Ak sa raz majú naozaj používať,
patria do pravidiel, ktoré píše NEX Studio.

## Ako to bolo overené

Nie plánom, ale **skutočným založením projektu**: vznikli dva stavové súbory,
dva priečinky denníkov, žiadna mŕtva rola — a pravidlá sa na ten výsledok
úspešne doplnili pre obe živé role.

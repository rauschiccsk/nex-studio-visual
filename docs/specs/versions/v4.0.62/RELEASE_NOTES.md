# v4.0.62 — NEX Studio Visual

## Ceny modelov na jednom mieste

Ceny tokenov boli rozsypané do ôsmich samostatných kariet pod sebou — na prečítanie troch čísel bolo treba prejsť niekoľko obrazoviek.

Teraz je z toho **jedna tabuľka**: riadok na model, v ňom cena vstupu a cena výstupu vedľa seba.

| Model | Cena vstup | Cena výstup |
|---|---|---|
| Haiku | … | … |
| Sonnet | … | … |
| Opus | … | … |

Pod nimi zostáva **záložná cena pre neznámy model** — použije sa vtedy, keď model nie je jeden z tých troch alebo sa nepodarilo zistiť, ktorý to bol. Nechali sme ju viditeľnú zámerne: záložná cena, o ktorej nikto nevie, je presne to, ako vznikne nesprávny výpočet, ktorý si nikto nevšimne.

## Tabuľka upozorní, keď cena v skutočnosti neplatí

Cena modelu sa použije, **len keď sú vyplnené obe polovice** — vstup aj výstup. Keby si vyplnil len jednu, výpočet ju potichu preskočí a použije záložnú cenu. Riadok na to teraz upozorní priamo pri sebe.

Rovnako je označená cena, ktorá nie je nastavená (nula alebo záporné číslo). Nie je to cena nula — je to chýbajúci údaj, a na obrazovke Náklady sa namiesto neho ukáže pomlčka.

## Drobnosť

Pri chybe načítania Nastavení sa hlásenie zobrazovalo trikrát pod sebou, akoby zlyhali tri rôzne veci. Teraz je raz.

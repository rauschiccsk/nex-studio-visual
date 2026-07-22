# v4.0.28 — NEX Studio Visual

## Oprava: hotová úloha sa už nezasekne na neškodnom detaile

Keď AI Agent nahlási svoju hotovú, uloženú prácu, NEX Studio ju teraz **správne rozpozná** aj vtedy, keď k identifikátoru pripojí aj **krátky popis** (nielen samotné ID). Doteraz to v ojedinelom prípade mohlo spôsobiť, že **dokončená a správna úloha vyzerala ako „zlyhaná"** a build sa zastavil, hoci reálne bolo všetko v poriadku.

Od tejto verzie sa build na tomto detaile **už nezastaví** — pokračuje ďalej. (Súvisí s v4.0.27, ktorá takéto technické zádrhy nástroja aj tak nasmeruje na vývojára namiesto teba.)

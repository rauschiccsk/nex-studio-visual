# v4.0.99 — podčiarkne sa to, čo je naozaj preklep

Kontrola pravopisu bola v kokpite **vypnutá všade**. Preklep sa nepodčiarkol
tiež — funkcia sa tým neopravila, len zahodila.

## Príčina bola inde, než sme si mysleli

Tvrdilo sa, že prehliadač po slovensky nevie. Vie. Slovenčina je v jeho zozname
jazykov, len s vypnutou kontrolou pravopisu, kým angličtina ju má zapnutú.
Slovenský text sa teda celý čas kontroloval **anglickým slovníkom** — a preto
bolo podčiarknuté každé správne napísané slovo. Chyba bola v nastavení jazyka,
nie v jazyku.

## Nové pravidlo je jedno, ale podľa druhu políčka

- kde sa píšu **vety** — popis, správa pomocníkovi, telo dokumentu, poznámka —
  je kontrola **zapnutá** a políčko si vypýta slovenský slovník;
- kde sa drží **označenie** — krátky názov, port, verzia, súbor, cena, heslo,
  hľadaný výraz — je kontrola **vypnutá**, lebo tam podčiarkovanie len ruší.

Žiadne políčko na písanie textu už nie je nerozhodnuté: každé si vyberá vedome.

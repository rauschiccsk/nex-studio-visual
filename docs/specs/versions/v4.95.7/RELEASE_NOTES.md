# v4.95.7

**Verdikt Audítora je v rozhovore jedenkrát, aj keď kokpit k nemu pridá vlastné zistenie.** Keď kontrola nepoužívaného
kódu, kontrola slovenčiny v kóde alebo kontrola návodu na skúšku v UAT pridali k verdiktu svoju vetu, rozhovor
ukázal ten istý odsek dvakrát: raz od Audítora a raz s dodatkom kokpitu. Pri neúspechu to vyzeralo ako dva samostatné
problémy (Author Asistent 0.1.0). Verdikt sa teraz pripojí k Audítorovej správe z toho istého kola a ukáže jeho
zhrnutie aj zistenia kokpitu. Správa z predošlého kola sa už nikdy neprepíše, ani keď má rovnaký text.

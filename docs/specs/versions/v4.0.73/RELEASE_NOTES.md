# v4.0.73 — NEX Studio Visual

## Prázdny projekt už nevznikne potichu

Keď je vypnuté automatické zakladanie priečinkov, aplikácia doteraz projekt **vytvorila aj tak** a ohlásila úspech — hoci nevznikol priečinok, pravidlá agenta, git ani kontrolný beh. Chyba sa ukázala až pri prvom zostavení, hláškou, ktorá radila projekt založiť znova. Tá rada nemohla pomôcť, lebo znovuzaložením vznikol rovnako prázdny projekt.

Teraz sa zakladanie **odmietne rovno** a povie prečo aj čo s tým. Zároveň zostáva použiteľné na svoj pôvodný účel — prevzatie projektu, ktorý na disku už existuje.

## Neúspešné zakladanie už nezablokuje názov projektu

Keď zakladanie zlyhalo v polovici, priečinok zostal na disku, ale záznam v aplikácii nie. Ten názov projektu sa tým stal **nepoužiteľným navždy**: nový pokus narazil na zvyšky a aplikácia nemala tlačidlo, ktorým by ich upratala.

Po neúspechu sa nedokončený priečinok odteraz odstráni, takže rovnaký názov ide skúsiť znova.

# v4.0.98 — aj programovanie beží za zavretými dverami

## Databázu na skúšky dodá NEX Studio, nie pomocník

Programovanie potrebovalo prístup k správe kontajnerov jedine preto, aby si
spustilo databázu, proti ktorej bežia skúšky. To je veľké oprávnenie za malú
potrebu — kto ho má, môže si spustiť čokoľvek a čokoľvek pripojiť.

NEX Studio preto databázu **pripraví samo** ešte pred začiatkom práce, na
vlastnej oddelenej sieti, a pomocníkovi podá len adresu. Po skončení ju zahodí.
Prístup k správe kontajnerov už nedostáva a nevzniká ani žiadna okľuka k nemu.

Tri fázy z piatich sú tým zavreté vo vlastnom priestore. Vizuál a Verifikácia
stavajú celú aplikáciu, takže ani toto ešte nie je celé riešenie — a nebude sa
tak tváriť.

## Bezpečnostná previerka to zastavila dvakrát

Obe diery našla nezávislá previerka a obe sa overili naživo, nie na papieri:

- výber databázového obrazu sa riadil súborom **z projektu**, takže zavretá
  stavba si mohla sama určiť, čo NEX Studio stiahne a spustí s plnými právami
  na tej istej sieti, kde beží ona sama;
- meno na sieti sa dalo poskladať tak, aby ukazovalo inam, než malo.

Opravené obe, a nie tak, že sa zakáže známy zlý tvar — povolené je odteraz len
to, čo je výslovne prípustné.

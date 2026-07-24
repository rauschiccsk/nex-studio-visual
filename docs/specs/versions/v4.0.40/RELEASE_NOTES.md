# v4.0.40 — NEX Studio Visual

## Zakladanie, stavba aj nasadenie projektu z cockpitu

Posledný krok zakladania projektu (príprava CI runnera) padal, lebo serverové prostredie nemalo nástroj **docker** — a keďže bola zapnutá voľba „Automaticky zostaviť a nasadiť", zhodilo to celé zakladanie.

- **Doplnený docker** — backend teraz vie sám cez docker stavať, testovať aj nasadzovať projekty. Vďaka tomu zvládne používateľ **celý oblúk sám z cockpitu** (založenie → stavba → nasadenie), bez ručných zásahov.
- **Poistka** — aj keby niektorý z „dobrovoľných" krokov po založení (CI runner, smoke test) zlyhal, **nezhodí to už celé zakladanie** — projekt vznikne a daný krok sa dá dokončiť neskôr.

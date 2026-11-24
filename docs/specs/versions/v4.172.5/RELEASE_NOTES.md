# v4.172.5

**Povinná zmena hesla v NEX Manageri už nie je „pokazený vstup“.** Po nasadení na UAT kokpit skúša vstup do aplikácie
za človeka, ktorý nasadzoval. Keď si mal tento človek v NEX Manageri zákazníka najprv zmeniť heslo, aplikácia ho
správne nepustila a kokpit hlásil, že je pokazený vstup a aplikáciu netreba odovzdávať používateľom. Teraz kokpit tento
prípad pozná vopred a povie: „Vstup do aplikácie kokpit neoveril: používateľ … si má v NEX Manageri zákazníka najprv
zmeniť heslo…“ — aplikácia aj jej vstup sú pritom v poriadku.

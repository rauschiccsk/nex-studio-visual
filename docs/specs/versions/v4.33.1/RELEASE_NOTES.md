# v4.33.1 — prevzatie inštalácie dobehne do konca

Prvé ostré prevzatie skončilo chybou 500 — a to najhorším možným spôsobom: priečinok sa **naozaj
prevzal**, kontajnery nabehli, ale obrazovka povedala „Prevzatie zlyhalo" a v evidencii nezostalo
nič. Práca bola hotová a produkt o nej nevedel.

Príčina bola jednoduchá: adresa bežiacej aplikácie sa čítala z miesta, kde nie je. Skladá sa —
rovnako ako pri bežnom nasadení — z názvu inštalácie.

Prevzatie teraz dobehne celé: zapíše sa záznam kto/kedy/čo, do hlásenia sa dostane, ktoré ručné
súbory boli odložené, aj upozornenia z prípravy priečinka.

## Prečo to stráže nechytili

Overil som čisté funkcie aj dialóg, ale samotného vykonávateľa prevzatia som nikdy **nespustil** —
a chyba bola práve v ňom. Pribudli preto tri tvrdenia, ktoré ho spúšťajú celý, s atrapami prípravy
aj nasadenia: že prebehne, že si adresu zloží sám, a že zlyhanie neprehlási za úspech.

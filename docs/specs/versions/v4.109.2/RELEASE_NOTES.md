# v4.109.2

**Nasadenie na existujúcu inštaláciu doplní novú premennú správne.** Keď nová verzia aplikácie prinesie nastavenie,
ktoré inštalácia ešte nemá, kokpit ho vyplní podľa tých istých pravidiel ako pri prvom nasadení — heslo administrátora
od zákazníka, tajomstvo náhodnou hodnotou — namiesto prázdnej hodnoty z príkladu projektu. Heslo administrátora od
zákazníka inštalácia prevezme pri každom nasadení, takže jeho zmena v Zákazníkoch platí od najbližšieho nasadenia.
Doteraz NEX Manager 1.5.0 na existujúcej inštalácii dostal heslo administrátora prázdne a prenos ľudí sa nespustil.

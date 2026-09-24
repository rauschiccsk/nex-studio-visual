# v4.40.9

Ostré nasadenie ide na server zákazníka — dovtedy sa cesta k nemu končila na polceste.

**Kokpit už vedel nasadzovať na cudzí stroj, ale nemal komu to povedať.** Overenie po nasadení, príprava priečinkov aj poistka proti prepísaniu cieľ prijímať vedeli; chýbal posledný článok, ktorý im ho odovzdá. Ostré nasadenie by preto ticho zbehlo na stroji, kde beží kokpit — teda tam, kde zákazníkova aplikácia vôbec nie je.

**Teraz sa cieľ číta pri zákazníkovi a prejde celou cestou** až k spusteniu, k overeniu aj k poistkám. Zákazník bez vlastného servera je naďalej bežný prípad: nasadzuje sa sem, ako doteraz.

**Testovacia inštalácia sa na cudzí server nezvezie.** Údaj pri zákazníkovi hovorí o ostrej prevádzke; skúšobná verzia beží vždy na stroji kokpitu. Keby to tak nebolo, prvá skúška by zasiahla do servera zákazníka.

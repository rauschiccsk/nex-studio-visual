# v4.40.10

Aplikácia si pri nasadení ponechá adresu, na ktorej ju zákazník nájde.

**Kokpit dovtedy vedel zverejniť aplikáciu jediným spôsobom** — jedno meno, jedna sieť, bez certifikátu — a mal ho zapísaný natvrdo. Skutočné inštalácie u zákazníkov sú ale zverejnené inak: jedna z nich je dostupná pod dvomi menami, každé s vlastným vstupom a vlastným certifikátom, na sieti, o ktorej kokpit nevedel. Nasadenie by tú adresu prepísalo svojou a zákazník by sa k aplikácii nedostal.

**Teraz sa adresa číta z bežiacej inštalácie**, rovnako ako priečinky s dokumentmi zákazníka alebo ručne pridelené siete — všetko sú to údaje, ktoré vie len ona. Keď si inštalácia svoju adresu nesie, kokpit k nej svoju **nepridáva**: dve adresy na tej istej aplikácii si odporujú a tá naša by navyše ukazovala do siete, ktorá na cudzom serveri nemusí existovať — vtedy by sa nespustilo vôbec nič.

**Nové inštalácie sa nemenia.** Kde zatiaľ žiadna adresa nie je, dostane tú našu, ako doteraz.

**A hlavička vygenerovaného súboru už hovorí pravdu** o tom, čo je pod ňou — dovtedy tvrdila sieť, ktorú ten súbor nepoužíva. Číta ju človek na serveri zákazníka.

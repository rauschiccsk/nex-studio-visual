# v4.40.32

Kokpit pri každej stavbe aplikácie odovzdá aj číslo zmeny v kóde, z ktorej ju stavia — pri skúške
spustenia, pri nasadení na test aj do ostrej prevádzky a pri skúške nového projektu. Aplikácia si tak môže
zapamätať, z čoho presne vznikla. Doteraz dostala len číslo verzie, a projekt, ktorý presný pôvod
vyžaduje, sa cez kokpit nedal zostaviť vôbec. Keď stavaný kód obsahuje neuložené zmeny, číslo to
povie tiež a nepredstiera čistú zmenu.

Záverečná kontrola aplikácie po spustení (skript projektu) teraz dostane tie isté nastavenia, s ktorými
kokpit aplikáciu na skúšku spustil. Doteraz siahala po nastaveniach z pracovného priečinka projektu, ktoré
bývajú neúplné, a mohla zlyhať hneď na prvom kroku, hoci aplikácia bola v poriadku.

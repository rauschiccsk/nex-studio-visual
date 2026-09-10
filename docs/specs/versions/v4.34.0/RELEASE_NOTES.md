# v4.34.0 — zaseknutú Verifikáciu sa dá zopakovať

Stavba NEX Managera 1.2.0 uviazla dvakrát po sebe. Obe príčiny sedeli v kokpite, nie v aplikácii —
tá bola po celý čas zelená.

## Stratená niť rozhovoru už nezablokuje vydanie

Audítorov beh padol na tom, že sa engine pokúsil nadviazať na sedenie agenta, ktoré neexistuje:

    claude exited with code 1
    No conversation found with session ID: 5eb01d07-…

Chýbajúca história rozhovoru nie je chyba aplikácie ani nález Audítora. Zadanie ťahu je sebestačné —
engine v ňom hovorí, čo sa má overiť. Zastaviť kvôli tomu bránu vydania znamená zdržať dobrú verziu
z dôvodu, ktorý s ňou nesúvisí.

Keď sa nadviazať nie je na čo, sedenie sa teraz **založí** a pokračuje sa — a **povie sa to nahlas**.
Nikto sa nemá dozvedieť až z výsledku, že agent pracoval bez pamäte. Skúša sa raz; opakovať to isté
volanie by nepomohlo, sedenie sa už neobjaví.

## Chýbajúce tlačidlo, ktoré bolo celý čas hotové

Keď Audítor nevrátil verdikt, kokpit napísal „usmerni alebo over znova" — ale **tlačidlo „over znova"
na obrazovke nebolo**. Manažérovi zostalo jediné: *Uprav*. To pošle opravného agenta hľadať chybu,
ktorá neexistuje. Agent spustil celú previerku, našiel ju zelenú a musel sa spýtať, čo má opravovať.

Pritom bolo hotové všetko: engine mal pre tento prípad vlastnú vetvu a rozhranie hotový pruh
s tlačidlom **Znova spustiť overenie**. Chýbal jediný krok — kokpit tú akciu nikdy neponúkol, takže
sa k nej nedalo dostať.

To je horší druh chyby než chýbajúca funkcia: postavená od začiatku do konca a nedosiahnuteľná.

Tlačidlo sa teraz ukáže presne vtedy, keď zlyhal **samotný overovací beh** — nie keď sa agent pýta
(tam treba odpovedať) a nie v iných fázach (tam sa overovanie opakovať nedá).

## A hlášky menujú tlačidlo, ktoré naozaj existuje

Text, ktorý posiela človeka hľadať neexistujúce tlačidlo, je horší než žiadna rada. Hlášky pri
zablokovanej Verifikácii teraz hovoria, že **zlyhal overovací beh, nie aplikácia**, a menujú tlačidlo
presne tak, ako je napísané na obrazovke.

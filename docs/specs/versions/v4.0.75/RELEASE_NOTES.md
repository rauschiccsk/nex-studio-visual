# v4.0.75 — NEX Studio Visual

## Zaseknuté stavby sa už dajú pohnúť

Keď stavba spadla na chybu rámca, obrazovka ponúkla **jediné tlačidlo** — a to sa pri stlačení skončilo chybou, lebo nebolo medzi povolenými akciami. Písanie bolo zároveň zamknuté, takže stavba sa nedala pohnúť žiadnym spôsobom.

Rovnako: keď kontrola zistila, že aplikácia nebeží, systém napísal „oprav to a spusti kontrolu znova" — ale tlačidlo **Skontrolovať** už bolo skryté a odmietané. Červená kontrola sa teraz dá zopakovať.

Pribudla aj kontrola, ktorá overuje, že **každá ponúkaná akcia sa dá vykonať**. Tá istá chyba tak už nemôže vzniknúť nepozorovane.

## Cudzí projekt to povie namiesto tichého čakania

Kto otvoril Riadiace centrum projektu, ku ktorému nemá prístup, videl pokojné „Voľný" — a spojenie sa donekonečna pokúšalo otvárať dvere, ktoré sa nikdy neotvoria. Teraz sa zobrazí, že prístup chýba, a pokusy sa zastavia.

## Prvé nasadenie k novému zákazníkovi už neblokuje ostrú prevádzku

Ak zákazník nemal prepojené prihlasovanie, nasadenie sa zapísalo ako **zlyhané** — hoci aplikácia bežala — a tým sa natrvalo zablokovalo nasadenie do ostrej prevádzky.

Nasadenie je teraz úspešné a chýbajúce prepojenie sa nesie ako **výstraha**. Pri tom sa oživil kanál výstrah, ktorý aplikácia vedela zobraziť, ale nikto doň nikdy nič nezapísal — skutočné výstrahy z nasadzovania sa teda doteraz zahadzovali.

## Chybný názov zákazníka sa odhalí pri zadávaní

Formulár prijal názov, ktorý nasadzovanie nedokáže použiť, a chyba sa ukázala až o dni neskôr ako zlyhané nasadenie. Teraz sa odmietne hneď a povie, čo je povolené. Existujúcim zákazníkom to nebráni v úprave — kontroluje sa to isté, čo kontroluje nasadzovanie.

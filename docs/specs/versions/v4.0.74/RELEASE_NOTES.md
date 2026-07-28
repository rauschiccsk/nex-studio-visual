# v4.0.74 — NEX Studio Visual

## Zlyhané nasadenie sa už netvári ako úspešné

Keď nasadenie zlyhalo — appka sa nerozbehla, rozhranie sa neozvalo, migrácia neprešla — obrazovka napriek tomu ukázala zelené **„✓ Nasadené"**. Dôvod zlyhania pritom prišiel v odpovedi zo servera, len sa naň nikto nepozrel.

Teraz sa zobrazí neúspech aj s dôvodom.

## „Zahodiť" už neničí prácu skôr, než zistí, že to nedokáže

Zahodenie zmien najprv **nenávratne zmazalo** neuložené úpravy a nesledované súbory — a až potom zistilo, že strom aj tak nie je čistý, lebo pripravené zmeny prežili. Zničilo teda prácu a oznámilo neúspech.

Poradie je obrátené: najprv sa overí, či sa dá zahodiť všetko, a až potom sa maže. Ak niečo prekáža, **nezmaže sa nič** a aplikácia povie, čo prekáža.

## Povýšenie knižnice konečne mení to, čo sa naozaj zostavuje

Tlačidlo prepisovalo číslo verzie, ale nedotklo sa zámku závislostí — a zostavenie sa riadi zámkom. Kokpit tak hlásil novú verziu, kým sa staval po starom. Rovnako aj **zobrazované číslo verzie** bolo len zapísané želanie, nie skutočnosť.

Obe sa teraz riadia zámkom. Keď sa povýšenie nepodarí dotiahnuť, **nezmení sa nič** a aplikácia to povie namiesto tichého polovičného stavu.

## Nečitateľné zadanie sa už netvári ako prázdne

Keď sa zadanie nepodarilo načítať — napríklad pri chýbajúcom prístupe — vykreslilo sa ako prázdne pole. Kto doň potom niečo napísal a uložil, **prepísal tým skutočný súbor**. Teraz je to rozoznateľný stav a uloženie je zablokované.

## Zakladanie verzie sa nespustí nad neovereným stromom

Keď kontrola neuložených zmien zlyhala, aplikácia na to upozornila — a tlačidlo nechala aktívne. Neznámy stav sa už neberie ako čistý.

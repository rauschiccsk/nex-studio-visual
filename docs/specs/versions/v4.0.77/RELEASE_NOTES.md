# v4.0.77 — NEX Studio Visual

## Zmazanie projektu už nezničí dáta potichu

Zmazanie projektu búralo aj jeho testovacie prostredie **vrátane databázy** — teda všetkého, čo do nej zákazník kedy zadal. Potvrdzovacie okno pritom hovorilo len o kontajneroch a porte, a poistka proti ničivému zmazaniu strážila **len ostrú prevádzku**.

Okno teraz vymenuje všetko, čo zmazanie odstráni: testovacie prostredie aj s databázou, evidenciu zákazníkov, históriu nasadení a prerušenie prebiehajúcej kontroly. A projekt, ktorého testovacie prostredie obsahuje dáta, sa **nedá zmazať** — treba ho archivovať.

## Nasadzovanie už neprepíše cudzie súbory

Nasadzovanie zapisovalo na cieľovú cestu bez toho, aby sa pozrelo, čo tam je — a tie cesty sa prekrývajú so **živými zákazníckymi prevádzkami**. Raz sa už stalo, že redeploy prepísal zákazníkovi verejné smerovanie.

Ak na cieli sedí nasadenie, ktoré NEX Studio nevytvorilo, **odmietne sa a nič nezapíše**. Rovnako pri priečinku, ktorého pôvod sa nedá preukázať — vlastníctvo sa musí dokázať, nie predpokladať.

## „Voľný port" konečne znamená voľný

Aplikácia posudzovala voľnosť portu len podľa vlastnej evidencie — a už raz sa pomýlila: pridelila port, ktorý dvanásť dní držala iná služba. Teraz sa pýta aj samotného stroja a **neznámy stav sa nepočíta ako voľný**. Keď to nevie zistiť, povie to namiesto ponúknutia čísla, ktoré si neoverila.

## Vyhľadávanie v Dokumentácii povie, čo mu chýba

Vyhľadávanie nefungovalo na žiadnej inštalácii — hľadalo služby na mieste, kde nebežia — a hlásilo len „Vyhľadávanie zlyhalo". Teraz je adresa nastaviteľná a nedostupná služba sa aj pomenuje.

## Menej miest, kde sa dá opraviť nesprávny súbor

Existovali dva recepty na zostavenie aplikácie a nasadzoval sa len jeden. Opravy v tom druhom sa do prevádzky nikdy nedostali. Zostal jeden.

Zmizli aj nastavenia, ktoré mali popis, ale nečítal ich žiadny kód.

# v4.13.0 — Verzia neprejde ako overená, keď sú kontroly projektu červené

NEX ProductCatalogs mal štyri dni po sebe červené kontroly na GitHube. Za ten čas
NEX Studio vyhlásilo **dve verzie za overené** a jedna z nich šla do testovacieho
prostredia. Nikto si toho nevšimol, lebo overovanie sa na kontroly **vôbec
nepozeralo**.

## Prečo

Kedysi to NEX Studio kontrolovalo. V staršej podobe existoval krok „vydanie",
ktorý po odoslaní zmien počkal na výsledok kontrol a pri červenom zastavil.

Keď sme prestavovali priebeh na dnešné štyri fázy, nasadzovanie sa z neho vybralo
von — a **spolu s ním vypadla aj tá kontrola**. Funkcia zostala v kóde ležať,
nikto ju už nevolal, ale pri čítaní vyzeralo všetko v poriadku.

Nestratili sme tú schopnosť preklepom. Stratili sme ju pri prestavbe a dva roky
nikto nezistil, že tam nie je.

## Čo sa mení

Než môže byť verzia vyhlásená za overenú, NEX Studio sa **spýta na stav kontrol**
pre práve overovaný stav kódu:

- **červené** → verzia neprejde a v hlásení stojí, ktorý beh padol
- **zelené** → pokračuje sa ako doteraz
- **ešte bežia alebo sa nedajú zistiť** → **neblokuje sa**, len sa to poznamená

To posledné je dôležité. Kontrola, ktorá zastaví aj vtedy, keď nič nevie, je
kontrola, ktorú sa ľudia naučia obchádzať. Zastavíme len na skutočnom neúspechu.

## Ešte jedna vec

Tú starú, nikdy nevolanú funkciu sme **zmazali**. Mŕtvy kód, ktorý popisuje
schopnosť, čo neexistuje, je horší než žiadny — presne on spôsobil, že sme si
štyri dni mysleli, že kontroly niekto stráži.

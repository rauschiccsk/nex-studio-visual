# v4.2.7 — oprava sa už neuzavrie na slovo agenta

## Opravu po previerke si stavba premeria sama

Keď previerka pred vydaním zistí, že sa aplikácia nespustí, vznikne opravná
úloha. Jej zadanie končí vetou *„…potom over znova"*. Doteraz to nikto
neskontroloval — úloha sa uzavrela, keď agent ohlásil hotovo.

Kontrola, ktorá po úlohe bežala, overuje, že zmeny sú zapísané a sľúbené súbory
ležia na disku. To je užitočné, ale na otázku **„naštartuje to?"** neodpovedá.

Od tejto verzie stavba na konci Programovania **sama spustí aplikáciu** — tou
istou skúškou, ktorá pôvodne zlyhala — a až podľa výsledku sa ťa spýta na
súhlas:

- **Nabehla** → do rozhovoru pribudne veta, že sa spustila, a až potom sa
  ponúkne *Prejsť na overenie*.
- **Nenabehla** → oprava sa **nepovažuje za hotovú**. Vráti sa medzi otvorené
  úlohy, skupina prestane svietiť „Hotovo", plán prestane hlásiť 100 % a na
  obrazovke stojí dôvod aj s výpisom z kontajnera.

## Prečo to bolo treba

29.08.2026 ukazovala obrazovka stavby `nex-productcatalogs` 125 zo 125 úloh
hotových, 100 %, deväť zelených skupín, správu *„Úloha #9.3.1 — hotovo
(1 pokus)"* a odporúčané tlačidlo *Prejsť na overenie*.

Aplikácia sa pritom nespúšťala — chýbala jej knižnica na príjem nahratých
súborov a jej hlavná časť umierala hneď pri štarte.

Nič na tej obrazovke neklamalo naschvál. Ale riadok, ktorý sa začína slovom
**Systém**, vyzeral ako strojové potvrdenie, hoci to bolo preposlané tvrdenie
agenta. Rozdiel medzi „stroj to odmeral" a „agent to o sebe napísal" nebolo
z obrazovky ako rozoznať.

Teraz je: čo je označené ako systémové, je odmerané.

## Čo sa nespomalí

Bežné kolo Programovania — také, ktoré nevzniklo zo zlyhanej skúšky — sa
nekontroluje navyše. Po ňom nasleduje previerka, ktorá aplikáciu spustí tak
či tak.

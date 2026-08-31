# v4.3.0 — „to isté zlyhanie" už karta povie len vtedy, keď to naozaj zmerala

## Čo sa stalo

Stavba narazila dvakrát za sebou. Karta ohlásila:

> ⚠️ Automatická oprava sa NEDARÍ — **to isté zlyhanie sa opakuje**.

a odporučila prácu **zastaviť a odovzdať vývojárovi**.

Neboli to tie isté zlyhania. Boli to dve úplne rôzne:

1. chýbala položka v menu — **chyba nástroja**, opravená hneď predtým
2. chýbala funkcia na strane servera — **skutočná medzera v projekte**

Prvá bola v tej chvíli už vyriešená. Stavba sa teda **pohla dopredu** — a obrazovka radila zastaviť ju.

Manažér sa podľa nej riadil a napísal: *„Nepochopil som, čo treba robiť."* Právom.

## Prečo sa to stalo

Kontrola **počítala neúspešné kolá**, nie ich príčiny. Z toho počtu potom vyslovila tvrdenie o **zhode** — že ide o to isté zlyhanie — hoci obsah tých zlyhaní vôbec neporovnávala.

Počítanie bolo správne. Veta postavená na ňom nie.

## Čo sa zmenilo

**Porovnávajú sa príčiny, nie pokusy.** Nová prekážka ukončí sériu rovnako ako úspech — naraziť na niečo iné znamená posun, nie opakovanie.

**Karta povie, čo sa zmenilo.** Keď je príčina iná než minule, pribudne veta: *„Oproti minulému kolu je to INÁ príčina — tá predošlá je vyriešená."* Práve to nadpis skrýval, lebo znie zakaždým rovnako. Keď sa nič nezmenilo, nepribudne nič — žiadny šum navyše.

**Tvrdenie o zhode zaznie len keď je overené.** Ak sa príčiny porovnať nedajú (staršie stavby dôvod nezaznamenávali), karta povie pravdivé *„niekoľko kôl po sebe neprešlo"* namiesto *„to isté zlyhanie sa opakuje"*.

## Čo zostalo

Poistka proti nekonečnej slučke funguje ďalej. Keď sa **naozaj** tá istá prekážka zopakuje trikrát, karta aj naďalej odporučí zastaviť a zavolať vývojára — a vtedy si to tvrdenie zaslúži, lebo ho zmerala.

Rovnako sa nič nemení pri stavbách, ktoré dôvod nezaznamenávajú: poistka sa spustí ako predtým, len sa vyjadruje opatrnejšie.

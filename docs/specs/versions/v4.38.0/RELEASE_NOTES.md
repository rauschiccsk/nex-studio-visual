# v4.38.0 — staršie projekty sa konečne dajú prevziať

Tlačidlo **Prevziať existujúci projekt** doteraz fungovalo len na projektoch, ktoré si NEX Studio samo
založilo. Pri každom staršom skončilo hláškou o porte mimo povoleného rozsahu — a to sú práve tie
projekty, kvôli ktorým to tlačidlo existuje.

## Prečo

NEX Studio prideľuje projektom porty podľa nášho štandardu — v blokoch po desiatich. Staršie aplikácie
vznikli skôr, než tento systém zaviedol, takže bežia na číslach mimo neho. Prevzatie sa ich pokúšalo
zapísať tak, ako ich našlo na disku, a vlastné pravidlo ich odmietlo.

## Čo je odteraz

**Pri prevzatí dostane projekt porty podľa štandardu** — rovnako, ako keby vznikal dnes. Prevziať
aplikáciu totiž znamená prevziať aj pravidlá, podľa ktorých sa u nás pracuje.

Bežiacej aplikácie sa to **nedotkne**. Tie čísla sú záznam v evidencii, nie nastavenie aplikácie;
tá počúva ďalej na svojom a chodí sa k nej rovnako ako predtým.

V náhľade pred potvrdením je vidieť oboje — čo projekt dostane aj na čom beží dnes:

```
Porty    10200 / 10201 / 10202  (backend / frontend / databáza)
         dnes beží na 8000 / 5173 / 5433 — blok vyššie je pridelený
         podľa nášho štandardu a na bežiacu aplikáciu nemá vplyv
```

Projekt, ktorý štandard **už spĺňa**, si svoje porty ponechá — prečíslovať niečo, čo bolo v poriadku,
by len rozišlo evidenciu so skutočnosťou.

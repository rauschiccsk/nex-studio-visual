# v4.6.0 — hotová verzia prestáva vyzerať ako nezačatá

Prvého septembra prešiel `nex-productcatalogs v0.1.0` celou stavbou. Príprava, Návrh,
Vizuál, Programovanie, Verifikácia s výsledkom PASS, tvoj podpis na Hotovo. Plán úloh
147 zo 147.

A v zozname verzií stálo: **Plánované.**

## Prečo to nebola kozmetika

Detail verzie sa podľa stavu rozvetvuje. Pri „plánovanej" ukáže panel **Zadanie** s návodom,
ako stavbu spustiť. Nad verziou, ktorá je postavená, overená a schválená, ti teda appka
ponúkala tlačidlo na spustenie toho, čo už dávno prebehlo.

A netýkalo sa to jednej stavby — **rovnako boli na tom všetky štyri dokončené verzie**
naprieč tromi projektmi. Ani jedna sa z „Plánované" nepohla.

## Čo bolo zlé

Verzia mala tri stavy: **plánovaná → prebieha → vydaná.** Znie to úplne, ale nie je.

Stav sa menil len na dvoch miestach a **ani jedno nesúviselo s dokončením stavby.** Na
„prebieha" ju prepínala ručná úprava celku v pláne — čo priebeh stavby nikdy nerobí. Na
„vydané" až nasadenie. Medzi tým nebolo nič, čo by zaznamenalo, že sa stavba stala.

Hotová a nezačatá verzia teda vyzerali rovnako, lebo v evidencii naozaj rovnaké boli.

## Čo sa mení

**Verzia má štvrtý stav — „Hotové".**

| stav | čo znamená |
|---|---|
| **Plánované** | ešte sa nezačalo |
| **Prebieha** | stavia sa práve teraz |
| **Hotové** | postavené, overené, tebou schválené — čaká na nasadenie |
| **Vydané** | nasadené |

Rozbehnutá stavba prepne verziu na **Prebieha** hneď pri prvom ťahu. Tvoj podpis na Hotovo
ju prepne na **Hotové**. Nasadenie potom na **Vydané**, ako doteraz.

Zvažoval som ponechať tri stavy a nechať hotovú verziu „prebiehať" až do nasadenia. Neurobil
som to zámerne: **potom by dokončená verzia vyzerala rovnako ako tá, ktorá sa práve stavia** —
tá istá chyba len prehodená inam.

## Čo z toho uvidíš

- Nad hotovou verziou **už nie je návod, ako ju spustiť.**
- V zozname projektov hotová verzia hovorí **„Otvoriť"**, nie „Pokračovať" — nie je čo pokračovať.
- **Hotové a Vydané majú rozdielnu farbu.** Nasadenie je samostatný krok a nemá vyzerať, že už prebehol.
- **Tie štyri dokončené verzie sa dorovnajú samy** pri nasadení tejto verzie.

## Ešte jedna oprava, ktorú nevidno

Stav verzie zobrazovali **dve obrazovky, každá s vlastnou kópiou** tej istej funkcie. Obe mali
prepad „čokoľvek neznáme = Plánované". Presne tak vzniká, že sa oprava spraví na jednom mieste
a druhé zostane pozadu.

Sú teraz na jednom mieste — a **neznámy stav sa už nevydáva za „Plánované".** Vypíše sa tak, ako
prišiel. Radšej nezrozumiteľné než nepravdivé.

---

*Prečo stredná číslica: pribúda stav, ktorý sa objaví na obrazovke a treba mu rozumieť. To nie je
oprava chyby, ale rozšírenie toho, čo evidencia dokáže povedať.*

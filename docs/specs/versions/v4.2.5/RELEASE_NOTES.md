# v4.2.5 — keď sa aplikácia nespustí, povie sa aj prečo

## Doteraz sa hovorilo len to, ČO spadlo

Skúška pred vydaním oznámila napríklad toto:

> aplikácia sa nespustila — `container … migrate-1 exited (1)`

Ktorý kontajner spadol. **Nikdy prečo.** Výpis tej služby — kde príčina stojí
čiernym po bielom — sa zahodil.

## Čo to spôsobilo

Na jednom projekte to stálo deň práce. Aplikácia sa nespúšťala, hlásenie
neprezradilo dôvod, a tak si ho **dvaja ľudia domysleli — každý inak a obaja
vedľa**. Jeden usúdil, že chýba adresa databázy, a opravil to. Druhý usúdil, že
databáza sa hlási zdravá priskoro, a opravil to. Obe opravy boli užitočné a
**ani jedna nebola tá pravá**: skutočná príčina — príliš dlhé označenie jednej
migrácie — ležala celý čas v tom zahodenom výpise a obe „opravy" prežila.

## Čo sa zmenilo

Keď skúška zlyhá, **priloží výpis tej služby, ktorá spadla**. Kto to číta —
človek aj pomocník — vidí príčinu, nie len následok. Zbieranie výpisu je
zámerne opatrné: keby zlyhalo aj ono, skúška sa správa presne ako doteraz.
Diagnostika nesmie položiť to, čo diagnostikuje.

## A jedna tichá chyba pri tom

Skúška si zostavuje vlastné nastavenia zo vzorového súboru projektu. Keď v ňom
adresa databázy zostala **prázdna** — čo je legitímne, ak si ju aplikácia
skladá sama — vyrobila z nej nezmysel `//ci@db` a vložila ho do **každej**
služby. Dnes to nič nekazí, lebo správne nastavenie prebíja to nesprávne; raz by
to však spôsobilo pád, ktorého príčina by sa hľadala úplne inde.

Prázdna hodnota zostáva prázdna.

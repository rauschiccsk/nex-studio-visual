# v4.107.2

**Opätovné nasadenie zapíše novú verziu do všetkých nastavení inštalácie, ktoré verziu nesú.** Doteraz sa v nastaveniach
inštalácie (.env) prepisovala len jedna z premenných s verziou aplikácie; ostatné ostali staré a aplikácia bežala v
správnej verzii len vďaka tomu, čo jej kokpit odovzdal pri spustení. Teraz nastavenia inštalácie popisujú presne to,
čo beží — aj keď niekto spustí inštaláciu na serveri ručne.

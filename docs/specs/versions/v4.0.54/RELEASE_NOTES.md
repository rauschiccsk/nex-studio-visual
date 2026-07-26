# v4.0.54 — NEX Studio Visual

## Nasadenie už nikdy nezhasne bez vysvetlenia

Keď sa v projekte po označení verzie za hotovú urobili ďalšie úpravy, tlačidlo **Nasadiť** sa samo zamklo — a obrazovka k tomu nepovedala ani slovo. Kto na to narazil, nemal ako zistiť prečo, ani ako sa pohnúť ďalej; pomohol až kolega cez príkazový riadok.

Odteraz obrazovka **UAT** aj **PROD** vždy povie, čo sa deje a čo s tým. Podľa situácie sa zobrazí jedna z hlášok:

- **Kód sa po označení „hotové" zmenil** — vysvetlíme, že by sa nenasadilo presne to, čo sa vyskúšalo, a ponúkneme tlačidlo **„Over znova"**. Aplikácia sa nanovo spustí a prekontroluje; ak je všetko v poriadku, verzia sa sama označí ako hotová a nasadenie sa odomkne. Stránka sa medzitým obnovuje sama — netreba nič klikať ani sledovať.
- **Kontrola prebehla dobre, chýba len schválenie** — verzia je krok od nasadenia, stačí ju potvrdiť.
- **Po označení „hotové" na verzii ešte prebehli práce** — treba ju nanovo prekontrolovať a označiť ako hotovú.
- **Na verzii sa práve pracuje** — nasadiť sa dá až po dokončení rozrobenej práce.
- **Žiadna verzia zatiaľ nie je hotová** — nie je čo nasadiť.

Tlačidlo **„Over znova"** sa zobrazí len vtedy, keď ho daný používateľ naozaj môže spustiť a keď v danom stave naozaj funguje. Nikdy sa nekreslí tlačidlo, ktoré by po kliknutí skončilo chybou.

## Opravené mätúce hlásenia na obrazovke nasadenia

- Zašednuté tlačidlo malo v bublinke text **„Nasadiť verziu X"** — teda tvrdilo pravý opak toho, čo robilo. Bublinka teraz sedí so skutočným stavom.
- Keď sa nasadenie nepodarilo, appka hlásila **„akceptačná brána"** aj na UAT, kde žiadna akceptačná brána neexistuje. Teraz sa zobrazí skutočný dôvod.
- Riadok s vysvetlením pod tlačidlom sa doteraz zobrazoval len na PROD. Teraz je na oboch obrazovkách.

# v4.97.1

**Oprava: živý náhľad Vizuálu ukazuje aplikáciu s knižnicami, ktoré má teraz.** Keď AI Agent vo Vizuáli povýšil alebo
pridal knižnicu, náhľad ďalej ukazoval starý stav — knižnice si zabalil pri spustení a tlačidlo „Spustiť náhľad znova“
bežiaci náhľad nechalo tak. Náhľad si teraz pamätá, s akými knižnicami sa spustil, a keď sa zmenia, kokpit ho sám
spustí nanovo: na konci každého kola Vizuálu ešte predtým, než ťa pošle pozrieť sa, a pri kontrole náhľadu v Riadiacom
centre. Náhľad sa pri tom na pár sekúnd odmlčí a potom sa sám načíta znova. Keď sa ho nepodarí spustiť, uvidíš „Živý
náhľad nebeží“ s tlačidlom „Spustiť náhľad znova“.

Po nasadení tejto verzie sa každý náhľad, ktorý práve beží, raz spustí nanovo — staršie náhľady si knižnice ešte
nepamätali.

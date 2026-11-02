# v4.108.0

**Ostré nasadenie, ktoré zlyhá po výmene kontajnerov, vráti inštaláciu na predošlú verziu.** Kokpit si pred výmenou
zmeria stav databázy (revíziu migrácií). Keď potom zlyhá migrácia alebo aplikácia neodpovedá a databáza ostala
v pôvodnom stave, vráti predpis a nastavenia spred nasadenia, spustí predošlú verziu z jej obrazov a overí, že
odpovedá — hlásenie to povie jednou vetou. Keď sa databáza zmenila alebo jej stav nepoznáme, nič nespúšťa a povie, že
návrat znamená obnovu zálohy. Doteraz inštalácia v takom prípade stála, kým niekto nenasadil predošlú verziu ručne.

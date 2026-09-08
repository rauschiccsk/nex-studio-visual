# v4.24.0 — jeden ťah agenta už nezhasne celý kokpit

## Čo sa stalo 7. septembra

O 19:33 prestalo NEX Studio odpovedať. Nie pomaly — vôbec. Žiadna chyba, žiadna
výnimka v protokole, posledný zápis z času tesne pred zaseknutím. Jedinou stopou bola
hromada zaseknutých kontrol zdravia vnútri kontajnera: 16 po dvadsiatich minútach,
65 po štyridsiatich. Pomohlo až reštartovanie.

## Prečo

Rozbehnutie živého náhľadu vo fáze Vizuál spúšťa `npm install` (strop 10 minút) a dva
dockerové príkazy (po minúte). Bežalo to **na tom istom vlákne, ktoré obsluhuje celý
server** — takže kým to trvalo, backend neodbavil nikoho. Vrátane vlastnej kontroly
zdravia.

Nebola to teda chyba v zmysle pádu. Bola to normálna práca na nesprávnom mieste.

To je aj dôvod, prečo bola taká zákerná: ťah, ktorý spadne nahlas, sa dá vyšetriť.
Ťah, ktorý sa zahryzne a stiahne so sebou celý kokpit, vyzerá ako výpadok siete —
manažér nemá čo nahlásiť a nemá kde hľadať.

## Čo je odteraz inak

Práca, ktorá čaká na proces, beží vo vlastnom vlákne a má strop. Hlavná slučka zostáva
voľná, takže kokpit odpovedá aj počas nej.

Keď sa náhľad nerozbehne do pätnástich minút, ťah sa usadí a povie to — a povie to
**inak než pri zlyhaní**: príprava možno ešte beží. Bez toho rozdielu by sa manažér
ponáhľal spúšťať niečo, čo už beží, a rozbehol by druhý ťah popri prvom.

## Stráž, ktorá to drží

Na disciplínu sa tu spoľahnúť nedá — ten istý omyl sa pri ďalšej funkcii spraví znova
a prejaví sa až o mesiac ako „NEX Studio nejde“. Pribudla preto stráž, ktorá prehľadá
celý backend a nájde každé miesto, kde sa z hlavnej slučky priamo čaká na proces.

Pri prvom písaní mala tá stráž presne tú dieru, ktorú stráži: zoznam modulov, do
ktorých sa pozerá, bol vypísaný ručne. Keď som ho pri skúške naschvál oslepil — vyhodil
z neho práve ten modul, cez ktorý kokpit spadol — zostala zelená. Zoznam sa teraz
porovnáva so skutočnosťou, takže nový modul nemôže vzniknúť mimo dohľadu.

## Čo zostáva otvorené

Tá istá choroba je v engine-i ešte na 46 miestach — gitové príkazy volané priamo
z hlavnej slučky, so stropmi 15 sekúnd a pri odosielaní vydania 60. Rádovo kratšie,
ale rovnaké. Vlastný tiket **ICCINT-82**; 46 zmien v najcitlivejšom súbore sa nedá
poctivo overiť spolu s touto opravou, tak sa tu zámerne nemiešajú.

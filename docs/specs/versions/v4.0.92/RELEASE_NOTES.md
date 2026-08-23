# v4.0.92 — porty z jednej evidencie a sedem opráv, ktoré vyšli z používania

Táto verzia zhŕňa prácu z 21. augusta. Všetky opravy vznikli tak, že sa pri
bežnom používaní narazilo na niečo, čo nefungovalo — nie z plánu.

## Porty

- **Evidencia portov je jedna, nie štyri.** Kokpit ju číta zo znalostnej bázy
  namiesto toho, aby si držal vlastnú kópiu. Štyri kópie sa nevyhnutne rozídu
  a potom nikto nevie, ktorá platí.
- **Pridelený blok sa zapisuje späť** do evidencie. Bez toho by ho ďalší projekt
  dostal druhýkrát.
- **Zmena portu na existujúcom projekte sa kontroluje** rovnako prísne ako pri
  zakladaní. Predtým sa nekontrolovala vôbec.

## Nastavenia a projekty

- **Projekt sa dá po založení upraviť.** Dovtedy bola hodnota zadaná pri
  zakladaní konečná — projekt zapísaný na cudzom bloku portov sa musel opraviť
  priamo v databáze, lebo iná cesta neexistovala.
- **Nastavenie, ktoré nepatrilo do žiadnej skupiny, sa nezobrazovalo vôbec.**
  Nebolo skryté zámerne — jednoducho vypadlo.

## Prihlásenie

- **Obnovenie prihlásenia sa po jednom neúspechu už neprestane pokúšať.** Krátky
  výpadok backendu predtým vyhodil používateľa, ktorý mal prihlásenie platné
  ešte hodiny.
- **Vybratý projekt zostáva vybratý aj po opätovnom prihlásení** a je viazaný na
  používateľa — nikto nevidí, čo si pripol niekto iný.

## Ostatné

- **Zlyhaná ochrana hlavnej vetvy to povie na obrazovke**, nie len do denníka.

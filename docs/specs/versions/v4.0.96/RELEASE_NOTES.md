# v4.0.96 — nasadzovanie prestalo klamať

Dve veci, ktoré tvrdili nepravdu.

## Verzia, ktorú appka o sebe hlásila

Backend hlásil verziu **4.0.78**, hoci bežal na v4.0.94. Údaj v nastavení
nasadenia zamrzol a odvtedy prešlo **šestnásť nasadení** — celý ten čas o sebe
tvrdil verziu, ktorou nebol.

Posúva sa odteraz automaticky spolu s nasadením. Verzia, ktorá je správna len
vtedy, keď si na ňu niekto spomenie, bude skôr či neskôr nesprávna.

## Skript na nasadenie

Skript v repozitári nasadzoval z adresára, ktorý na tomto stroji **neexistuje**,
staval obrazy s inými menami, než aké tu bežia, a používal inú schému verzií.
Prišiel sem pri odštepe z iného projektu a nikdy tu neplatil.

Dnes by spadol na chýbajúcom súbore. Keby ten adresár niekto obnovil zo zálohy,
postavil by **druhý beh aplikácie vedľa toho živého, nad tou istou databázou** —
a to je tvar poruchy, po ktorej už raz prestali fungovať verejné adresy.

Nový skript robí presne ten postup, ktorý sa dovtedy robil rukou, a **odmietne
bežať**, keď cieľ nesedí: chýbajúce nastavenie, žiadny bežiaci beh aplikácie,
alebo — to hlavné — keď to, čo beží, pochádza z iného nastavenia než z toho, do
ktorého sa chystá zapisovať.

## Poznámky k vydaniu, ktoré chýbali

Verzie v4.0.84 až v4.0.95 nemali v tejto záložke záznam. Doplnené spätne
z toho, čo sa v nich naozaj zmenilo.

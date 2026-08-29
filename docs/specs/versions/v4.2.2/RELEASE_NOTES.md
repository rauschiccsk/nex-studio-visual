# v4.2.2 — rozpísaný text sa už nestratí (a tri upratania)

## Rozpísaná správa prežije odchod z obrazovky

Kto napísal správu pomocníkovi a preklikol na Dokumenty, aby si niečo overil,
našiel po návrate **prázdne políčko**. Bez varovania. Preklik pritom nie je
odbočka od práce — je to jej súčasť; človek ide overiť, čo práve píše. Aplikácia
ho trestala presne za to správanie, ktoré od neho chceme.

Netýkalo sa to len rozhovoru — **žiadne** z písacích políčok si text neuchovalo.
Najhoršie to bolo pri úprave návrhu od technického tímu: úpravy zmizli, ale
pôvodné znenie zostalo pripravené na odoslanie, takže sa dala odoslať verzia,
ktorú už človek zavrhol, v presvedčení, že posiela svoju.

Text sa teraz priebežne ukladá a po návrate vráti. Zmaže sa **až po úspešnom
odoslaní**, nikdy pri odchode. Obnovený text je označený — aby bolo zrejmé, že
je to vlastný starší koncept, a nie niečo cudzie.

## Upozornenie na procesor prestalo kričať pri jednom percente

Hlásenie „vyťaženie nad 80 %" v skutočnosti merilo **jedno jadro**, nie stroj.
Na osemdesiatjadrovom serveri sa teda spúšťalo pri **jednom percente** výkonu —
pri každej stavbe, donekonečna. Meria sa odteraz podiel celého stroja a stavebné
kontajnery sú z pravidla vynechané: spotreba procesora je pri nich očakávaná
a krátkodobá.

Hlásenie, ktoré chodí vždy, naučí človeka hlásenia ignorovať — vrátane toho
jedného, ktorý raz bude dôležitý.

## Stavebný kontajner sa už nehlási ako chorý

Izolovaný priestor beží na obraze aplikácie a niesol si aj jeho kontrolu zdravia,
ktorá v ňom nemá čo hľadať. Zlyhávala každých pár sekúnd po celý čas práce.
Nič sa podľa nej neriadilo, ale kto sa pozrel na bežiace kontajnery, videl
„chorý" a hľadal poruchu, ktorá neexistuje.

## Pravidlo o portoch platí vždy, nielen niekedy

Rozloženie portov v bloku projektu (backend, frontend +1, databáza +2) sa
kontrolovalo **len vtedy**, keď sa menil aj port backendu. Kto menil iba
frontend, prešiel bez kontroly. Pravidlo, ktoré platí podľa toho, ktoré políčka
človek pošle, nie je pravidlo. Teraz sa porovnáva aj s uloženou hodnotou.

Hláška pri odmietnutí bola navyše po anglicky a hovorila o stĺpcoch v databáze —
odteraz je po slovensky a hovorí o portoch.

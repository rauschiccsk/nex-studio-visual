# v4.40.19

Nasadenie na server zákazníka po sebe upratá.

Odkedy sa aplikácie stavajú priamo u zákazníka, zostávajú po každej stavbe pomocné súbory. Za deň a pol zabrali dvanásť gigabajtov a na jednom serveri sa ozval hlásič porúch, že dochádza miesto. Upratovať to ručne by znamenalo vrátiť človeka do práce, ktorú mu má automatické nasadzovanie odobrať.

**Po úspešnom nasadení sa teraz nepotrebné súbory odstránia samy** a koľko miesta sa uvoľnilo, sa napíše do hlásenia o nasadení.

**Po neúspešnom nasadení sa neupratuje nič.** Vtedy je dôležitejšia cesta späť než miesto na disku — a práve staršie verzie aplikácie sú tou cestou. Z toho istého dôvodu zostávajú nedotknuté aj vtedy, keď nasadenie prebehne: maže sa len to, čo už nikto nedrží.

**A keby sa upratanie nepodarilo, nasadenie to nezhodí** — aplikácia beží, len sa neuvoľnilo miesto. Napíše sa to do hlásenia, aby o tom človek vedel.

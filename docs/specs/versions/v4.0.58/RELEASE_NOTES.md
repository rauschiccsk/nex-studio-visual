# v4.0.58 — NEX Studio Visual

## Appka, ktorá sa nedá otvoriť, sa už nenasadí ako „hotovo"

Niektoré aplikácie sa neotvárajú menom a heslom, ale jedným kliknutím z NEX Managera. Aby to fungovalo, musí mať aplikácia aj Manager rovnaký podpisový kľúč — NEX Studio ho pri nasadení doplní.

Rozpoznávalo si to však podľa toho, či aplikácia spomenie presne dohodnutý názov nastavenia. Keď si ho pomenovala po svojom (čo sa stalo pri NEX Websites), NEX Studio ju za takú aplikáciu nepovažovalo, kľúč jej nedoplnilo a vyrobilo jej náhodný vlastný — taký, aký nikto iný nepozná. **Nasadenie pritom ohlásilo úspech.** Že sa appka nedá otvoriť, sa ukázalo až o hodiny neskôr, keď na tlačidlo „Spustiť" klikol kolega — a odblokovať to musel niekto cez príkazový riadok.

Odteraz to NEX Studio kontroluje na dvoch miestach:

- **Pred nasadením** overí, či aplikácia potrebné nastavenia vôbec deklaruje. Ak nie, nasadenie sa ani nespustí a rovno vypíše, ktoré názvy jej treba doplniť. Nič sa pritom nezhodí — bežiaca inštancia zákazníka zostáva nedotknutá.
- **Po nasadení** si sám skúsi vystaviť vstupenku na otvorenie appky — presne tak, ako to robí tlačidlo „Spustiť". Ak to nejde, nasadenie sa označí ako **neúspešné** aj vtedy, keď sa appka technicky rozbehla. Bežiaca appka, do ktorej sa nikto nedostane, nie je hotové nasadenie.

## Chybové hlášky pri nasadení už hovoria k veci

Keď nasadenie odmietlo pokračovať, aplikácia to zhrnula ako **„zadané údaje nie sú v poriadku"** — a konkrétne vysvetlenie, čo presne treba doplniť, skryla do technického detailu, ktorý sa nikde nezobrazoval. Teraz sa zobrazí priamo tá zrozumiteľná veta. Týka sa to aj staršej kontroly na chýbajúce heslo administrátora, ktorá mala rovnaký problém.

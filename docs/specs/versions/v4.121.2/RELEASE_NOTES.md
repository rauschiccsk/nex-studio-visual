# v4.121.2

**Aplikácia, ktorú NEX Manager už pozná, dostane pri nasadení nový kľúč, keď ho jej inštalácia nemá.** Aplikácia na
šablóne sa pri vstupe pýta NEX Managera, kto je človek, a preukazuje sa vlastným kľúčom. NEX Manager ho vydá len raz,
pri registrácii. NEX Websites zaregistrovalo nasadenie verzie 1.0, ktorá kľúč nepoužívala, takže sa nikam neuložil a
verzia 1.1.0 sa z NEX Managera nedala otvoriť („Aplikácia ešte nie je prepojená s NEX Managerom“). Teraz si kokpit pri
nasadení vyžiada od NEX Managera nový kľúč, zapíše ho do inštalácie a ohlási „Aplikácia sa znova prepojila s NEX
Managerom zákazníka“. Inštalácii, ktorá kľúč má, sa nič nemení. Keď NEX Manager kľúč nevydá, nasadenie sa nezastaví
a hlásenie povie, že stačí neskôr kliknúť „Nasadiť znova“.

Hlásenie o opätovnom prepojení teraz menuje všetko, čo sa v prepojení oproti stavu pred nasadením zmenilo — aj údaj,
ktorý nová verzia pridala.

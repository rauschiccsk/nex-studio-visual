# v4.41.1

Poradca vidí v databáze UAT ešte menej, než mal: tabuľky s prístupmi (napríklad uložené prihlásenie
k e-mailovej schránke v NEX Inboxe, prihlásenia používateľov, jednorazové tokeny) nedostane vôbec,
ani v zašifrovanej podobe. Účet len na čítanie, ktorý Poradcovi do databázy UAT otvára cestu, sa teraz
pri každom nasadení UAT správne zosúladí so schémou — predtým by sa jeho príprava v živých inštaláciách
nepodarila.

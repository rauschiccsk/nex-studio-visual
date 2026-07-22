# v4.0.30 — NEX Studio Visual

## Spustenie appky priamo z karty UAT

Pri appkách, ktoré sa spúšťajú cez NEX Manager (prihlásenie tokenom), viedol odkaz „Otvoriť aplikáciu" na holú adresu, kde appka len oznámila „spusti z NEX Managera" — otvoriť sa nedala. Teraz na karte **Nasadenie** pribudlo pri takej appke tlačidlo **Spustiť**:

- **Jedno kliknutie = prihlásená appka** — NEX Studio vytvorí krátkodobý testovací prístup na strane servera a otvorí nasadenú UAT appku rovno prihlásenú, bez obchádzky cez NEX Manager.
- **Platí len pre UAT a len pre appky s prihlásením tokenom** — ostré (PROD) prostredie sa naďalej spúšťa výhradne cez NEX Manager; appky s klasickým prihlásením menom a heslom majú ako doteraz odkaz „Otvoriť aplikáciu".
- **Bez slepých uličiek** — tlačidlo „Spustiť" je aj hneď po nasadení, takže sa už nestane, že po nasadení klikneš na odkaz a naďabíš na hlášku „spusti z NEX Managera".

Bezpečnosť: testovací prístup je platný len pár desiatok sekúnd, je jednorazový a nevystupuje ako reálny používateľ — je jasne označený ako testovací.

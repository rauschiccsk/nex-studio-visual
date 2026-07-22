# v4.0.31 — NEX Studio Visual

## Oprava: tlačidlo „Spustiť" na karte UAT

V predošlej verzii sa po kliknutí na **Spustiť** appka neotvorila a zobrazila sa nejasná hláška „zadané údaje nie sú v poriadku". Táto verzia to opravuje:

- **Appka sa spustí správne** — chyba bola v tom, že systém hľadal nastavenia nasadenej appky pod nesprávnym tvarom mena zákazníka (rozlišovali sa veľké a malé písmená, hoci nasadený priečinok je vždy malými písmenami). Spustenie teraz používa rovnaký tvar mena ako celé nasadzovanie, takže appku spoľahlivo nájde a otvorí prihlásenú.
- **Zrozumiteľné hlásenia** — ak by spustenie predsa len nešlo, na karte sa po novom ukáže **konkrétny dôvod a čo s tým** (napr. „launch kľúč pre UAT nie je nastavený" alebo „nie je to token-launch aplikácia — použi Otvoriť aplikáciu") namiesto všeobecnej hlášky.

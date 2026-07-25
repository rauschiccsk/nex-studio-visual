# v4.0.44 — NEX Studio Visual

## Živý náhľad (Vizuál) sa spustí aj pre novo založené projekty

Pri novom projekte sa fáza **Vizuál** zasekla — živý náhľad sa nepodarilo spustiť. Priečinok projektu totiž zakladá server pod svojím účtom (správca), no náhľad beží pod bežným účtom a do takého priečinka si nevedel pripraviť potrebné súbory (chýbali práva) — preto zlyhal.

Opravené:

- **Nové projekty** — po založení sa priečinok projektu automaticky nastaví na správne vlastníctvo, takže živý náhľad sa spustí bez zásahu.
- **Existujúci projekt**, ktorý na tento problém narazil, bol opravený, takže Vizuál sa v ňom dá spustiť znova.

Pre používateľa to znamená, že cesta **Príprava → Návrh → Vizuál** prejde plynulo aj pri úplne novom projekte.

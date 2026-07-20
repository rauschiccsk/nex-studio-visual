# v4.0.17 — NEX Studio Visual

## Testovacie prostredie generovaných appiek si teraz vždy založí databázu

Ukázalo sa, že pri záverečnom overení sa **testovacia databáza spustila prázdna** — nevytvorili sa v nej tabuľky, tak skúška spadla hneď na prvom kroku. Bola to najčastejšia a najzákernejšia príčina blokovaného vydania (chybová hláška vyzerala ako problém so spojením, hoci chýbala len príprava databázy).

Od tejto verzie:

- **Šablóna záverečnej skúšky obsahuje povinný krok, ktorý založí schému databázy** (spustí migrácie) ešte pred samotnými testami. Každá nová appka ho tak má od začiatku.
- **Pokyny pre AI Agenta to výslovne pripomínajú** — testovacie prostredie štartuje s prázdnou databázou, schému treba vytvoriť.

Vďaka tomu už žiadny projekt nezablokuje „chýbajúce tabuľky" v testovacom prostredí.

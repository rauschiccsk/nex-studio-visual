# v4.0.34 — NEX Studio Visual

## Vlastník projektu sa vyplní automaticky

Pri zakladaní nového projektu bolo pole **Vlastník** (kto dostáva Telegram notifikácie od agenta) pre bežného používateľa prázdne — ponúkalo len „— žiadny —". Dôvod: zoznam používateľov vidí len správca. Po novom:

- **Bežný používateľ** (nie správca) zakladá projekt vždy **pre seba** — Vlastník je predvyplnený jeho menom a zobrazený ako needitovateľné pole („ty"). Žiadny prázdny výber.
- **Správca** má naďalej výber, kde vlastníka priradí komukoľvek (alebo nikomu).

Založenie projektu fungovalo aj predtým (server nastavil vlastníka na zakladateľa), toto opravuje mätúce zobrazenie.

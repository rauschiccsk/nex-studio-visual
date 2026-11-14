# v4.140.4

**Audítor ani Poradca už nebudú hlásiť heslo dočasnej databázy stavby ako riziko.** Pri programovaní dostane
Staviteľ na každý ťah vlastnú dočasnú databázu s náhodným heslom a po ťahu databáza zanikne. Keď sa jej heslo
objavilo vo výstupe, Audítor odporúčal overiť, či sa nepoužíva aj inde, a Poradca to po ňom opakoval — zbytočný
poplach, ktorý mohol zdržať overenie stavby. Pravidlá Audítora aj Poradcu to teraz vysvetľujú (Staviteľ to vo svojich
pravidlách mal); skutočné prístupy projektu (heslá inštalácií, kľúče, tokeny) chránia ďalej rovnako prísne.

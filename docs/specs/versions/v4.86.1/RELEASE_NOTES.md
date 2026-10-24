# v4.86.1

**Kontrola nepoužívaného kódu spozná výnimku pre trasu aj podľa mena jej funkcie.** Trasy, ktoré volajú iné
aplikácie (napríklad rozhranie NEX Managera pre NEX Knowledge), majú v projekte zapísanú výnimku. Kontrola ju doteraz
spoznala len pod názvom „GET /cesta“ a výnimku s menom funkcie potichu prehliadla — verziu potom zastavila s pokynom
trasu odstrániť. Teraz platí aj meno funkcie, a výnimku, ktorá nič nepokrýva, kontrola nahlas ohlási.

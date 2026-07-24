# v4.0.36 — NEX Studio Visual

## Oprava: zakladanie projektu padalo na chýbajúcom git

Založenie nového projektu končilo chybou „chyba na strane servera". Príčina: serverový balík (image) neobsahoval nástroj **git**, ktorý sa používa pri vytváraní projektu (založenie repozitára, prvý commit) aj pri ďalších operáciách so zdrojmi. Doplnené — zakladanie projektu teraz prejde celým procesom.

# v4.0.67 — NEX Studio Visual

## Písmo konečne vyzerá tak, ako malo

Aplikácia si od začiatku pýtala písmo **Inter**, ale nikdy ho nedodala — v štýloch nebolo ani jedno pravidlo, ktoré by ho načítalo, a žiadny súbor s písmom. Prehliadač ho teda nenašiel a použil systémové písmo tvojho počítača. Na Linuxe to býva tenká fontina, ktorá pri malých veľkostiach a našej diakritike vyzerá zle.

Písmo sa teraz **dodáva priamo s aplikáciou**. Vyzerá rovnako na každom počítači bez ohľadu na to, čo má kto nainštalované, a nesťahuje sa odnikiaľ zvonku — je u nás. Znaky ako ľ, š, č, ť, ž, ô alebo ď majú vlastnú časť, ktorá sa načíta len keď treba.

## Potlačený text je čitateľnejší

Sivý pomocný text mal vo svetlom režime kontrast pod normou pre bežné čítanie. Opravené — a to priamo v spoločnej výbave, nie záplatou v tejto aplikácii.

Ukázalo sa totiž, že **všetkých päť našich aplikácií** si tú istú chybu opravovalo samostatne, každá po svojom. Chyba bola v spoločnej výbave a každá aplikácia za ňu platila zvlášť. Teraz je opravená pri zdroji a záplaty sa môžu postupne odstrániť.

# v4.40.30

Automatické kontroly kódu (CI) pre projekty, ktoré kokpit založil, sa už nezastavia, keď GitHub vydá novú
verziu svojho runnera. Kokpit doteraz runner zakladal s pevnou verziou a so zakázanou aktualizáciou. Keď
GitHub 24. septembra túto verziu prestal prijímať, runnery sa už nemohli pripojiť: dookola sa spúšťali
a končili a CI pre nové projekty sa nemalo kde spustiť. Teraz sa runner aktualizuje sám, rovnako ako runnery
priamo na serveri.

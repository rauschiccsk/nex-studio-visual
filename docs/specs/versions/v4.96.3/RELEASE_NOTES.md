# v4.96.3

**Verdikt Verifikácie je v rozhovore veta, nie samotné slovo „FAIL“ či „PASS“.** Keď kokpit zastaví verziu pre
červené kontroly projektu na GitHube, rozhovor teraz povie „Verifikácia neprešla pre kontroly projektu na GitHube —
…(ktorý beh)… Kým nebudú zelené, verzia sa nedá vyhlásiť za overenú.“ Doteraz tam stálo len „FAIL“ a dôvod bol
v skrytom technickom detaile (Author Asistent 0.1.0). Ručný verdikt sa zapíše ako „Verifikácia prešla (ručné
rozhodnutie)“ alebo „neprešla“, a keď kokpit ručné „prešla“ zrazí pre červenú skúšku po spustení, povie to.

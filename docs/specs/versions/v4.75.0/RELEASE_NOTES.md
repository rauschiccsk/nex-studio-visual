# v4.75.0

**Upratovanie kokpitu — preč je, čo nič nepoužívalo.** Na obrazovke sa nič nemení. Z kokpitu zmizlo staré rozhranie
z jeho prvej verzie (chyby, epiky, featy a úlohy na zakladanie agentom, ktorý už neexistuje), ďalších 25 volaní
servera, ktoré žiadna obrazovka nevolala, dve nepoužívané súčasti obrazovky, kód, ktorý volali už len skúšky, a súbory,
ktoré nič nečítalo (starý predpis nasadenia, maketa, vyradená charta, nástroje prvej verzie, tri knižnice). Kokpit má
menej kódu na údržbu a stráženie. Dve nové kontroly v každej zmene zabránia, aby sa to vrátilo: každé volanie servera
musí mať volajúceho a kód obrazovky nesmie žiť len v skúškach.

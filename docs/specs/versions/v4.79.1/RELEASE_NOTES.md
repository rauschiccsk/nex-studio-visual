# v4.79.1

**Poznámky k vydaniu sa zapíšu do gitu aj so súborom, ktorý si z nich robí aplikácia.** Keď kokpit pri úspešnej
Verifikácii zapíše poznámky k vydaniu, pregeneruje aj súbor, ktorý si z nich aplikácia zostavuje (napríklad zoznam
zmien v NEX Inboxe). Ak ten súbor pred zápisom poznámok niečo už zmenilo — napríklad stavba aplikácie —, kokpit ho
doteraz vynechal a kontrola projektu na GitHube potom zlyhala. Teraz do commitu ide každý súbor, ktorý generátor
prepísal; rozpracované súbory agenta, na ktoré generátor nesiahol, ostávajú mimo, ako doteraz.

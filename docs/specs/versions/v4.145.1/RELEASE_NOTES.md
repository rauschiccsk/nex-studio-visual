# v4.145.1

**Pole v Riadiacom centre si po návrate drží svoju výšku.** Keď stavba čakala na tvoju odpoveď Staviteľovi a vložil si
do poľa text od Poradcu, po prechode do Poradcu a späť sa pole zúžilo na dva riadky a text sa zle čítal. Pole sa totiž
ukáže až po načítaní stavu stavby a jeho výška sa vtedy neprepočítala. Teraz sa výška prepočíta vždy, keď sa pole
objaví alebo zmení šírku — platí to pre každé pole, ktoré rastie s textom (Riadiace centrum, Poradca, Nápady).

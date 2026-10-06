# v4.43.0

**Náklady počítajú celú spotrebu agentov, podľa cenníka Anthropic.** Doteraz kokpit videl len vstupné
a výstupné tokeny; to, čo agent pri každom kroku znova číta z vyrovnávacej pamäte, chýbalo — a to býva
väčšina ceny. Teraz sa ráta všetko, aj ťah, ktorý vypršal alebo ho niekto zastavil. Cenník si kokpit zistí
sám z ťahov, ktoré zaplatil Claude Code, a kurz eura stiahne z Európskej centrálnej banky; pri každej verzii
v Nákladoch je vidieť, akými cenami a akým kurzom sa počítalo. Sumy sú v celých eurách zaokrúhlených nahor.
Ručné ceny modelov z Nastavení zmizli — nepočítalo sa nimi už nič. Kde sa staršiu spotrebu nedá doložiť
záznamom, Náklady napíšu „nevyčíslené" aj s dôvodom.

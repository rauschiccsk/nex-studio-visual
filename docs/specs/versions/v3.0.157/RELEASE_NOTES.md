## v3.0.157 — Nasadenie: priebeh, potvrdenie a odkaz na živú appku

Nasadenie k zákazníkovi trvá 1–2 minúty a doteraz o tom cockpit mlčal — len malé koliesko, žiadne potvrdenie, žiadny odkaz na výslednú appku. Odteraz:

- počas nasadzovania sa ukáže **„Nasadzujem… (~2 min, počkaj)"**,
- po dokončení **potvrdenie „✓ Nasadené"** a **klikací odkaz „Otvoriť aplikáciu"** — pre UAT aj **PROD** (predtým PROD odkaz úplne chýbal),
- ak prvé produkčné nasadenie **povýši verziu projektu**, cockpit to oznámi,
- prípadné **upozornenia** z nasadenia sa zobrazia po slovensky,
- pri UAT pribudol odznak **„Akceptované ✓"**, aby manažér videl, ktoré verzie už akceptoval (a neakceptoval ich omylom dvakrát).

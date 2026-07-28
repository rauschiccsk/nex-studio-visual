# Záverečný audit v4.0.79 — stav opráv

Audit: 82 agentov, 10 tried chýb, každý nález overený nezávislým skeptikom.
**70 navrhnutých → 53 potvrdených, 17 zamietnutých pri overení.**

Plný zoznam s dôkazmi: `closing-audit-findings.md` (číslovanie [1]–[53] je záväzné).

## Hotové

| skupina | opravené | commit |
|---|---|---|
| závažné | **10 z 10** | `5c5b010` |
| stredné | **28 z 28** | `0a564c8`, `48bcd79`, + záverečná dávka |
| kozmetické | **15 z 15** | `0a564c8`, + záverečná dávka |

Nasadené ako **v4.0.80**, CI zelené (behy 30380006910, 30381616881), kokpit
aj backend overené naživo.

## Zostáva

Nič z auditu. Všetkých 53 potvrdených nálezov je opravených.

Otvorené sú len tri veci, ktoré nie sú opravy, ale **rozhodnutia Manažéra**:

1. Obrazovka „Členovia projektu" — `GET /users` je len pre Manažéra, kým `POST /project-members`
   zvládne aj vlastník. Treba rozhodnúť, ktoré právo platí.
2. `ProjectMember.role` je voľný text bez slovníka povolených hodnôt.
3. Osem živých inštalácií (vrátane MÁGERSTAV-u a tohto kokpitu) vzniklo ručne a nemá hlavičku
   „spravuje kokpit", takže ich nová poistka chráni, ale kokpit ich nevie znovu postaviť.
   Otázka znie, či ich prevziať pod hlavičku, alebo nechať natrvalo ručné.

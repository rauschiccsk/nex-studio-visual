# v4.0.59 — NEX Studio Visual

## Agenti bežia na novom modeli Opus 5

Kokpit doteraz spúšťal AI Agenta aj Audítora na modeli Opus 4.8 — bolo to zapísané priamo v kóde, takže sa to nedalo prepnúť z obrazovky. Odteraz je predvoleným modelom **Opus 5** a v Nastaveniach si ho môžeš pre každú rolu vybrať zo zoznamu (Opus 4.8 v ňom zostáva, keby si sa chcel vrátiť).

Výpočet nákladov na to reaguje sám — nový model spadá do rovnakej cenovej rodiny ako predošlý, takže prehľady zostávajú správne bez ďalšieho nastavovania.

## Zoznam modelov sa už nemôže rozísť

Frontend mal vlastný, ručne písaný zoznam povolených modelov. Keď pribudol nový, zoznam v aplikácii o ňom nevedel — a rozdiel sa ukázal až pri kontrole typov. Teraz sa zoznam preberá priamo z toho, čo hlási backend, takže každý ďalší model sa objaví na obrazovke sám a rovnaká chyba sa nemôže zopakovať.

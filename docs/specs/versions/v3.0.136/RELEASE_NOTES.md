## v3.0.136 — Overenie: appka sa spustí aj s migráciami databázy

Predošlá oprava zabezpečila, že sa appka v overovacom teste rozbehne, no jej **databázové migrácie** ešte nedostávali kompletné nastavenia a padali (chýbal im údaj o pripojení k databáze). Teraz overovacie prostredie dodá kompletné hodnoty **aj dovnútra všetkých kontajnerov** — nielen do konfigurácie compose. Migrácie prejdú a appka sa naštartuje celá.

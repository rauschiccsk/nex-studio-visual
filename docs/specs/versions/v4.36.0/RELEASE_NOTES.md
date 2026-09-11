# v4.36.0 — vyhľadávanie v dokumentácii už nezaostáva za skutočnosťou

## Čo bolo zle

Keď si niečo zapísal do Znalostnej bázy, vyhľadávanie sa to nedozvedelo. Nie o chvíľu — **vôbec**.
Z 205 dokumentov ich 113 nesedelo s tým, čo je naozaj na disku: 71 vyhľadávanie nepoznalo a ďalších
42 poznalo v podobe starej celé týždne.

Najhoršie na tom nebolo to zaostávanie. Bolo to, že sa o ňom **nedalo dozvedieť**. Spýtal si sa,
dostal si odpoveď, vyzerala normálne — a pochádzala zo sveta spred dvoch mesiacov.

## Čo je odteraz

**Zapíšeš a pokračuješ.** Vyhľadávanie sa dorovná samo, zvyčajne do štvrťhodiny, a po štarte
aplikácie hneď. Nič nespúšťaš a na nič si nepamätáš.

**A vidíš, ako na tom je.** V hlavičke Dokumentácie je jedna veta:

- *vyhľadávanie sedí* — pozná všetko v aktuálnej podobe,
- *vyhľadávanie nesedí: N dokumentov* — koľko ešte nestihlo; po nabehnutí myšou uvidíš aj ktoré,
- *vyhľadávanie: nedá sa zistiť* — služba vyhľadávania nebeží.

Tá tretia je tam zámerne. Keď sa stav zistiť nedá, appka to **povie** — namiesto toho, aby mlčala
a nechala ťa v presvedčení, že je všetko v poriadku. Prehliadanie a čítanie dokumentov funguje aj
vtedy; nefunguje len vyhľadávanie.

## Prečo to píšeme aj sem

Toto nebola náhodná chyba. Predpis roky hovoril, že po každom zápise treba vyhľadávanie obnoviť —
a odkazoval na návod, ktorý nikde neexistoval. Nikto to teda nerobil, hoci všetci mali.

Poučenie sme si zapísali priamo do pravidiel: **čo sa musí robiť po každej zmene, nemá robiť
človek.** Odteraz to robí aplikácia a jej prácu je vidieť.

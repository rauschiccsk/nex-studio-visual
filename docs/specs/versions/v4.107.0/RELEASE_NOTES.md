# v4.107.0

**Na šablónu aplikácie sa dá previesť aj projekt, ktorý už beží u zákazníka.** Karta „Previesť na šablónu aplikácie“
už takýto projekt neodmieta. Verzia, ktorou prejde do rámu šablóny, dostane v Príprave povinnú položku „Prenos dát do
rámu šablóny“: agent v Špecifikácii popíše, čo sa z vlastných tabuliek projektu prenesie do rámu (používatelia,
nastavenia) a čo sa vedome zruší (relácie, použité lístky — ľudia sa prihlásia znova otvorením z NEX Managera); údaje
aplikácie ostanú tie isté a tým istým ľuďom. Pred nasadením na PROD kokpit databázu zálohuje.

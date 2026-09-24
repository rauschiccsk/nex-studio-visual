# v4.40.8

Číslo verzie v aplikácii konečne hovorí o tom, čo naozaj beží.

**Panel ukazoval verziu obrazoviek ako verziu celej aplikácie.** Pravidlo, ktoré má pri rozdiele ukázať obe polovice, bolo v aplikácii od 14. septembra — a nefungovalo. Číslo backendu sa do prehliadača nemalo ako dostať: aplikácia sa naň pýtala na adrese, ktorú si odchytí server s obrazovkami a odpovie na ňu sám, bez verzie. Panel teda o backende nedostal nič a poctivo spadol späť na to jediné číslo, ktoré mal — číslo obrazoviek.

**Teraz ho backend vydáva na vlastnej adrese** a obe čísla sa píšu menom: pri rozdiele `backend v4.40.8 · frontend v4.40.2`, pri zhode jediné číslo, ako doteraz. Dve rovnaké čísla vedľa seba by pri každom bežnom nasadení len zaberali miesto.

**Tá istá oprava aj na prihlasovacej obrazovke.** Tá písala číslo obrazoviek ako verziu celej aplikácie — a vidí ju každý, kto ešte nie je vnútri.

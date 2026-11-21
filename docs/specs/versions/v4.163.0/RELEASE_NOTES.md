# v4.163.0

**Zmena v opise schémy databázy už nežiada schválenie.** Pri návrhu verzie kokpit porovnáva so schválenou schémou
v Znalostnej báze len štruktúru — tabuľky, stĺpce, kľúče, kontroly, indexy a kód. Keď sa zmenil len úvod alebo
vysvetľujúca veta, návrh už nehlási, že mení štruktúru databázy, a schválenie od Ri nežiada. Počet riadkov, ktoré
pribúdajú a ubúdajú, sa ráta tiež len zo štruktúry. Predtým stačila nová hlavička dokumentu (Orthodox Hermenia 0.2.0).

**Pamäť Staviteľa sa uloží sama.** Poznámky, ktoré si Staviteľ zapísal do svojej pamäte projektu a neuložil, kokpit
uloží do gitu, keď sa zakladá nová verzia. Nepočítajú sa medzi neuložené zmeny a „Zahodiť“ ich nezahodí. Predtým sa
na ne kokpit pýtal ako na tvoje neuložené zmeny (Orthodox Hermenia pri rýchlej oprave po verzii 0.2.0).

**Opakované povýšenie šablóny je uložené.** Keď povýšiš šablónu na verziu, ktorú projekt už má uloženú, kokpit to
berie ako uložené a nehlási neuložené zmeny. Po povýšení v Nastaveniach projektu si panel „Zadanie od Deda“ na tej
istej stránke načíta ponuky povýšenia znova a starú už neponúka.

**Plane tikety poznajú verziu, ktorá tiket rieši.** Tiket, ktorý menuje zadanie niektorej verzie projektu (zadanie
rýchlej opravy alebo zadanie verzie), má odznak „Vo verzii vX“ a rýchlu opravu už neponúka. Predtým to platilo len
pre rýchlu opravu spustenú priamo pri tikete (NEX HelpDesk: DEV-434 a DEV-412).

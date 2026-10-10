# v4.43.16

**Poradca vie povedať, ako dopadlo zostavenie.** Keď sa ho opýtaš, či prešli kontroly projektu alebo prečo
zlyhali, pozrie sa naň sám: povie, ktorá kontrola to bola, dá odkaz na ňu a pri zlyhaní prečíta aj koniec jej
záznamu. Doteraz sa mu to pre chybu v kokpite nikdy nepodarilo.

**Plán úloh ukazuje, koľko práca stála.** Pod každým EPIC, FEAT a úlohou je riadok ako pri odpovedi Poradcu,
napríklad „Trvalo 11 min 24 s · cena 4,05 € · 12,3 mil. tokenov“. Čas je len práca agenta (bez čakania na teba),
cena sa ráta rovnako ako v Nákladoch a keď sa časť práce nedá oceniť, napíše sa „cena aspoň …“. Po podržaní myši
nad riadkom sa ukáže rozpis tokenov.

**Súpis dodaných tokenov — podklad k faktúre za vývoj.** V Nákladoch pri vybranej verzii je súpis toho, čo
verzia naozaj dodala: kód, skúšky a dokumentácia, spočítané v tokenoch verejným štandardom o200k_base, ktorý je
priamo v kokpite. Rátajú sa len pridané a zmenené riadky. Čo sa neráta (Zadanie, zámky závislostí, vygenerované
súbory, zrkadlo špecifikácie, pracovné poznámky agenta), je uvedené aj s dôvodom. Oprava chyby v dodanom kóde
sa neúčtuje — pri zakladaní verzie a rýchlej opravy sa preto volí druh práce. Sadzby (kód a skúšky, dokumentácia)
sa nastavujú v Nastaveniach; vydaný súpis si sadzby pamätá a dá sa stiahnuť ako CSV. Pri súpise je aj to, koľko
práca agenta na verzii stála na 1 000 tokenov.

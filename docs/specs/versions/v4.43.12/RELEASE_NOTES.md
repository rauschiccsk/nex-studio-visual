# v4.43.12

**Pri Vizuáli vieš, čo v ňom skontrolovať.** Keď AI partner dokončí prvý návrh Vizuálu, pred odkazom na náhľad
uvidíš krátky zoznam: ktorú obrazovku otvoriť, čo na nej urobiť a čo máš vidieť. Pri každej položke je odkaz rovno
na tú obrazovku. Zvlášť je napísané, čo verzia mení, ale vo Vizuáli sa to ukázať nedá (napríklad e-maily) — to
schválením Vizuálu neschvaľuješ. Po každej zmene, ktorú si vyžiadaš, dostaneš zoznam toho, čo skontrolovať znova.
Položky si môžeš odškrtnúť a pri tlačidle „Schváliť vizuál“ vidíš, koľko z nich si už prešiel. Poradca vidí ten
istý zoznam, takže ho môžeš požiadať, aby Vizuál prešiel s tebou.

**Nový projekt má možnosti nastavenia už zaškrtnuté.** Automatická kontrola a zostavenie po každej zmene aj úplná
kontrola po zostavení sú pri zakladaní projektu zapnuté samy. „Chrániť hlavnú vetvu“ je zašednutá a pod ňou je
napísané prečo: GitHub ochranu pri súkromnom repozitári na našom pláne nedovolí. Keď sa plán zmení, zapne sa
v Nastavenia → GitHub → „Ochrana vetvy pri súkromnom repozitári“.

**Prvá kontrola nového projektu sa spustí hneď.** Pri zakladaní projektu kokpit najprv spustí vykonávač kontrol,
počká, kým sa prihlási na GitHube, a až potom tam pošle predpis kontroly. Doteraz to bolo naopak a prvá kontrola
čakala niekoľko minút, raz celý deň. Keď sa vykonávač nespustí alebo neprihlási, kokpit to pri založení povie
a predpis kontroly aj tak pošle.

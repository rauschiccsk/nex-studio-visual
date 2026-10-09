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

**Uložené zadanie sa dá upravovať.** Doteraz každé ďalšie uloženie zadania skončilo hláškou „Conflict“ — kokpit
odmietol akúkoľvek zmenu oproti tomu, čo už bolo na disku. Teraz si kokpit pamätá, z ktorého textu si vychádzal,
a tvoju úpravu uloží. Ak sa zadanie na disku medzitým zmenilo (napríklad ho zapísal niekto iný), ukáže ti ho
a ponúkne „Nahradiť mojím textom“, „Doplniť môj text na koniec“ alebo „Prevziať text z disku do poľa“ — bez tvojho
kliknutia sa nič neprepíše.

**Poradca vidí Zásobník.** Skôr než ti navrhne požiadavku do Zásobníka, pozrie sa, čo v ňom už je. Keď tam podobná
požiadavka je, povie ti jej číslo (napríklad REQ-1) a novú nenavrhne — tlačidlo „Uložiť do Zásobníka“ tak
nezaloží duplikát.

**Rozpísaný text sa pri odchode na inú obrazovku nestratí — nikde.** Zadanie na stránke verzie, zadanie novej
verzie, nový aj upravovaný dokument Znalostnej bázy, popis požiadavky v Zásobníku, poznámky k zákazníkovi, pokyn
pre rýchlu opravu a vlastná odpoveď na kartu rozhodnutia si pamätajú, čo si napísal; keď sa vrátiš, text je späť
s poznámkou, že je to tvoj rozpísaný text. Po uložení sa zabudne. Heslá a nastavenie integrácií sa v prehliadači
zámerne neukladajú. Každé nové pole na písanie musí odteraz o tomto rozhodnúť, inak kontrola kokpitu nepustí zmenu.

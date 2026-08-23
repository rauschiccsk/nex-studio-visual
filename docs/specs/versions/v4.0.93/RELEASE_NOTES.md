# v4.0.93 — keď sa stavba zasekne na chybe NEX Studia, dá sa pustiť ďalej

Doteraz to bola slepá ulička. Keď AI Agent narazil na chybu, ktorá nie je
v projekte, ale v samotnom NEX Studiu, stavba sa zastavila a ponúkala jediné
tlačidlo — „Nahlásiť znova", ktoré správu iba poslalo druhýkrát. Keď sa chyba
opravila, stavba zostala visieť **navždy**. Neexistovalo nič, čo by ju pohlo.

## Ako to funguje teraz

Náš technický tím dostane hlásenie, chybu opraví, napíše agentovi odpoveď
a stavbu odblokuje — **s dôvodom, ktorý je povinný** a zostane v rozhovore
natrvalo. Ty potom uvidíš zelený pruh s tým, čo sa opravilo, a **jedno tlačidlo**.
Keď ho stlačíš, agent dostane ťah a v ňom aj tú odpoveď.

## Prečo odblokovať nemôžeš ty

Nie z nedôvery. Nedá sa zvonku posúdiť, či bolo NEX Studio naozaj opravené —
a keby sa stavba pustila do tej istej pokazenej verzie, len by zhorela ďalšia
otáčka a znova by sa zastavila. Preto opravu hlási ten, kto ju robil,
a o pokračovaní rozhoduješ ty.

## Čo sa opravilo pri kontrole tejto zmeny

Tlačidlo bolo najprv **mŕtve vo fáze Vizuál** — stlačilo sa, nič sa nespustilo
a odpoveď sa stratila. Presne ten mŕtvy koniec, kvôli ktorému táto zmena vznikla,
len o obrazovku ďalej. Našli to dve nezávislé kontroly.

Zavreli sa aj bočné dvere: stavbu zablokovanú na neopravenej chybe sa dalo
rozbehnúť napísaním do rozhovoru. Teraz je pri chybe NEX Studia dostupné jedine
„Nahlásiť znova", nech sa o to pokúsi ktokoľvek a odkiaľkoľvek.

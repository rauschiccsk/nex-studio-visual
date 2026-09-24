# v4.40.13

Prvé ostré nasadenie už projektu nepriradí nižšie číslo, než aké nesie.

Kokpit má pravidlo, že prvé ostré nasadenie projektu ho povýši na verziu 1.0.0 — lebo do ostrej prevádzky sa ide z vývojových čísel 0.x. To pravidlo si však svoj vlastný predpoklad neoverovalo: projekt, ktorý je dávno na 1.2.2 a do kokpitu sa dostal až potom, čo už ostro bežal, by dostal označenie 1.0.0. Zákazníkovi by sa postavil novší kód a aplikácia by sa hlásila starším číslom.

**Teraz sa povyšuje len tam, odkiaľ je kam.** Projekt pod 1.0.0 sa povýši ako doteraz; projekt, ktorý je vyššie, sa nasadí pod vlastným číslom. Označiť verziu za vydanú treba v oboch prípadoch — mení sa len prečíslovanie.

Týka sa to každého projektu, ktorý preberáme spätne — teda takého, ktorý u zákazníka bežal skôr, než ho kokpit začal spravovať.

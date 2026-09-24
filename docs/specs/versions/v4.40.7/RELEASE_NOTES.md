# v4.40.7

Prevzatie cudzej inštalácie teraz hovorí pravdu o tom, čo by prepísalo.

**Náhľad číta inštaláciu tam, kde naozaj beží.** Doteraz sa pozeral do priečinka na stroji, kde beží kokpit — takže pri zákazníkovi s vlastným serverom ukazoval starú miestnu kópiu namiesto toho, čo tam beží dnes. Rozdiel bol v jednom nameranom prípade dva mesiace a celé smerovanie aplikácie.

**A namiesto hádania porovnáva.** Kokpit si vykreslí predpis, ktorý by na to miesto zapísal, priloží ho k tomu terajšiemu a vymenuje, čo by zmizlo: služby, siete, smerovanie, otvorené porty. Keď by zmizlo čokoľvek, prevzatie odmietne a povie čo — doteraz porovnával iba názvy vlastností, takže o inštalácii, ktorá by prišla o celú svoju adresu, tvrdil „nič sa nestratí“. Nová verzia aplikácie stratou nie je, tá sa nehlási.

**Keď sa na cieľový server nedá pozrieť, neprepisuje sa.** Nedostupný stroj sa už nečíta ako prázdny priečinok. A samotné čítanie po sebe na cudzom stroji nenechá nič — ani prázdny priečinok, ktorý tam predtým vzniknúť mohol.

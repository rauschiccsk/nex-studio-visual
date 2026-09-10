# v4.33.0 — prevzatie inštalácie sa robí v kokpite, nie v termináli

Nasadenie NEX Manager 1.1.0 na UAT pre MÁGERSTAV kokpit odmietol: v cieľovom priečinku ležalo
nasadenie, ktoré nevytvoril — ručne písané, živé, s vlastnými tajomstvami. Poistka zafungovala
správne a nesiahla na nič.

Zlá bola cesta ďalej: viedla do terminálu. A to je presne to, čo tento ekosystém nemá
vyžadovať — má zvládnuť celý priebeh od návrhu až po nasadenie.

## Čo pribudlo

Na obrazovke UAT a PROD je pri zlyhanom pokuse **Prevziať inštaláciu…**. Otvorí sa okno, ktoré
najprv **ukáže**, a až potom robí:

- ktorý priečinok to je,
- ktoré súbory sa odložia bokom a pod akým menom,
- čoho sa to nedotkne,
- **čo z toho priečinka práve beží** — najsilnejší dôkaz, že to nie je opustený zvyšok.

Potvrdzuje sa **odpísaním** `<zákazník>/<projekt>`, nie kliknutím. Priečinok sa u troch zákazníkov
volá rovnako, takže samotný názov by sa dal odpísať bez pozretia, koho inštalácia to je.

## Prečo je to bezpečnejšie než dovtedajší terminál

Pôvodná obava bola vecná: hlavička v súbore **je povolenie zapisovať**, a vpísať ju do ručne
písaného súboru zničí jediný dôkaz, že bol písaný ručne.

Preto sa tu nič nezničí — ručná práca sa **odloží** vedľa ako `.pre-nex-studio` (tá istá liečba,
akú dostali pravidlá agenta pri preberaní projektu). Dôkaz zostáva, krok sa dá vrátiť.

Ďalšie tri rozdiely oproti terminálu:

- **zapíše sa kto, kedy a čo prevzal.** Terminálové nasadenie nezapisovalo do evidencie nič —
  „bezpečnejší" spôsob bol ten, po ktorom nezostala stopa;
- **prihlasovacie údaje a dáta sa zachovajú.** Databáza v tom priečinku beží so svojím heslom;
- **vždy jedna inštalácia.** Hromadné prevzatie neexistuje.

## Čo sa nezmenilo

Tlačidlo **Nasadiť** túto možnosť nemá a nedostane ju. Prevzatie je oddelené rozhodnutie s vlastnou
obrazovkou a vlastným potvrdením — nikdy vedľajší účinok kliknutia na nasadenie. Stráž, ktorá to
drží (`allow_overwrite` sa v ceste bežného nasadenia nesmie vyskytnúť), platí ďalej a je teraz
zapísaná na dvoch miestach.

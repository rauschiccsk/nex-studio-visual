# v4.0.81 — zvyšné nálezy z auditu; kokpit je o 3,5 MB ľahší

Druhá a tretia dávka po hĺbkovej previerke. Týmto je zavretých všetkých 53
potvrdených nálezov.

## Ešte dve chyby z môjho vlastného rána

Keď som posunul prijatie spojenia tak, aby sa odmietnutie prístupu dostalo do
prehliadača, urobil som vedľa toho dieru:

- Pri zmazanej verzii začalo Riadiace centrum spojenie **prijímať a hneď zatvárať
  — raz za sekundu, navždy**, za prázdnou nástenkou. Teraz sa to zapamätá a
  obrazovka povie, že verzia už neexistuje.
- Neúspešná kontrola gitu **natrvalo zašednila „Uložiť Zadanie"** bez uvedenia
  dôvodu a bez čohokoľvek, čím by sa to dalo odblokovať. Priečinok bez histórie
  gitu je teraz odpoveď, nie chyba, a pri skutočnom zlyhaní je na obrazovke
  tlačidlo „Skúsiť znova“ aj vysvetlenie na zašednutom tlačidle.

## Obrazovky, ktoré chybu vydávali za skutočnosť

Úvodná stránka písala „Žiadne projekty — vytvor prvý" aj vtedy, keď sa ich
**nepodarilo načítať**. To isté robila Špecifikácia s dokumentmi, tabuľka
používateľov a tlačidlo „+ Nový prístup", ktoré pri chybe nerobilo vôbec nič.
Všetky teraz rozlišujú „zatiaľ nič" od „nepodarilo sa" a povedia to.

## Príkazy, ktoré sľubovali, čo sa nedeje

- „Automaticky zostaviť **a nasadiť**" — pritom šablóna kontroly žiadne
  nasadzovanie nemá. Teraz to hovorí pravdu.
- „Vývoj na zákazku" — hodnota sa uložila a nečítal ju nikto. Zašednuté s dôvodom.
- **Ochrana hlavnej vetvy na GitHube sa nikdy nezapla na žiadnom repozitári.**
  Posielala sa v zlom type a GitHub ju zakaždým odmietol; jediná stopa bolo
  varovanie v logu. Každý nový projekt tak mal hlavnú vetvu otvorenú.

## Nasadená appka je 1,5 MB namiesto piatich

Slovenský kontrolór pravopisu bol dávno vypnutý, ale build stále kopíroval **3,5 MB
slovník do každej nasadenej appky**. Spolu s ním odišlo vyše 2 800 riadkov mŕtveho
kódu aj s testami, ktoré ho testovali a predstierali pokrytie. Pokrytie testami
stúplo na 90 %.

## Brána vydania konečne vie spadnúť

Jej kontrola zdravia po tridsiatich pokusoch jednoducho skončila a krok prešiel —
takže stack, ktorý nikdy nenabehol, prešiel bránou, ktorá ho má zastaviť.

## Opravená dokumentácia

Návod posielal na porty, kde nič nepočúva, a do priečinka, ktorý neexistuje.
Runbook pre testovacie prostredie prikazoval ručný krok s nginx, ktorý skript už
roky nerobí. A charta Audítora si protirečila s vlastným runbookom: pravidlo „pri
rýchlej oprave stačí X.3 + X.4" znamenalo migrácie a spustenie — teda **bez**
overenia, že aplikácia vôbec odpovedá.

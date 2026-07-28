# v4.0.79 — drobné kozmetické nálezy z hĺbkového auditu

Posledná zo štyroch skupín opráv po komplexnom audite. Nič z toho nebolo vidieť
pri práci, ale každá položka bola nepravda v kóde alebo v dokumentácii — a mŕtve
veci sa časom čítajú ako živé.

## Merať začíname to, čo sme dovtedy nemerali

**Pokrytie testami sa prvýkrát v histórii projektu odmeralo — a stalo sa bránou.**
Nástroje na meranie boli v projekte nastavené od začiatku a **nespúšťal ich nikto**.
Nikto — ani Manažér, ani agent — nevedel povedať, akú časť 52-tisíc riadkov
backendu testy vôbec prejdú, a nič nebránilo tomu, aby toto číslo ticho klesalo.
Prvé meranie: **90 %**. Podlaha je nastavená na 88 %, aby bežné výkyvy nerobili
z fungujúcej sady červenú, a smie sa už len **dvíhať** — nikdy znižovať preto,
aby beh prešiel.

## Strojopisné písmo sa konečne aj dodáva

Rovnaká chyba ako pri Interi minulý týždeň, len o písmo nižšie: knižnica si
pýtala JetBrains Mono a Fira Code od prvého vydania a **ani jedno nikdy
nedodala**. Terminál agenta, bloky kódu v Špecifikácii a Znalostnej báze,
identifikátory požiadaviek aj čipy verzií sa preto vykresľovali tým, čo mal kto
na počítači — na každom stroji inak. Knižnica `nex-shared` v0.19.0 nesie
JetBrains Mono ako variabilné woff2 hosťované u nás.

## Mŕtve veci preč

- Dva frontendové moduly na rozhranie, ktoré **nevolal nikto** — zmazané.
- Údaj `coefficient_configured` z Nákladov: hovoril to isté, čo vedľajšie pole,
  a nikto ho nečítal. Preč z backendu, z rozhrania aj z typov.

## Nepravdivé texty opravené

- **Limit veľkosti dokumentu zo Znalostnej bázy sa vyhlasoval a nevynucoval.**
  Nastavenie `kb_content_max_bytes` existovalo a čítanie súboru ho obchádzalo —
  ktorýkoľvek veľký súbor sa načítal celý do pamäte. Teraz sa veľkosť overí
  pred čítaním a príliš veľký súbor sa slušne odmietne.
- **Nastavenia portov** tvrdili, že rozhodujú, kde appka počúva. Nerozhodujú —
  porty sú napevno v obrazoch kontajnerov. Text to teraz hovorí priamo.
- **CLAUDE.md §16** odkazovala na tri súbory, ktoré v tomto repozitári
  neexistujú. Pravidlo platí, adresy boli zlé.

# v4.28.0 — prevzatie existujúceho projektu jedným výberom

## Čo bolo zle

Prevzatie projektu, ktorý na disku už je, sa robilo formulárom pre **zakladanie nového**.
Manažér musel vedieť a ručne prepísať porty, adresu repozitára, typ aj spôsob
prihlasovania — a keď sa v niečom pomýlil, evidencia začala tvrdiť niečo iné, než je na
disku. Projekt pritom beží podľa disku, nie podľa evidencie.

A všetko to na disku už bolo. Formulár sa pýtal na odpovede, ktoré si vie sám prečítať.

## Ako to funguje teraz

V zozname projektov pribudlo tlačidlo **Prevziať existujúci**. Vyberieš priečinok — nič
nepíšeš naspamäť, ponúkajú sa len tie, ktoré na disku naozaj sú a kokpit ich ešte nepozná.

Systém si prečíta:

- **názov** z prvého nadpisu v `CLAUDE.md`,
- **porty** z `docker-compose.yml`,
- **adresu repozitára** z nastavenia gitu.

Potom ukáže, **čo našiel**, a až po tvojom potvrdení projekt preberie. Prevzatie je zápis
do evidencie, ktorý má sedieť s realitou — musí byť vidieť predtým, nie potom.

## Čo sa nikdy nehádа

Spôsob prihlasovania sa z disku spoľahlivo zistiť nedá. Neháda sa: vypíše sa medzi
nedopovedaným a ponúkne na potvrdenie. Uhádnutý údaj by v prehľade vyzeral ako zistený
a odklikol by sa bez pozretia — a to je horšie než prázdne políčko.

To isté platí pre všetko ostatné: čo sa prečítať nepodarí, sa vypíše. Nezrozumiteľná
väzba portu sa preskočí, nie odhadne.

## Prečo to čítanie nie je triviálne

Zmerané na štyroch skutočných projektoch a ani jeden spôsob nestačil sám:

- **nex-manager** — služby `backend` / `frontend` / `db`, vnútorné porty 8000 / 80 / 5432
- **nex-studio** — tie isté mená, ale vnútorné porty 9176 / 9177; podľa čísla by sa rola
  neurčila
- **nex-payables** — frontend sa volá `web` a väzba je `"127.0.0.1:10220:8000"` s poznámkou
  za ňou; podľa mena by sa rola neurčila a trojdielny tvar by sa rozobral zle

Preto sa berie meno služby, a keď nepomôže, vnútorný port.

Názov sa berie z `CLAUDE.md` a **nie z `package.json`** — tam stojí doslova „frontend".

## Stráže

Skúšajú sa proti **skutočným štyrom projektom**, nie proti vymyslenému vzoru. Vymyslený
vzor by potvrdil len moju predstavu o tom, ako tie súbory vyzerajú — a práve tá predstava
bola pri portoch dvakrát nesprávna.

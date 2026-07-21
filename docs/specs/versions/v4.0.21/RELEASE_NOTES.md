# v4.0.21 — NEX Studio Visual

## Nasadenie samo pripraví databázu (migrácie) — aj pre aplikácie bez migračnej služby

Aplikácia, ktorá si databázu spravuje cez migrácie (Alembic), potrebuje po nasadení tabuľky reálne vytvoriť/aktualizovať. Aplikácie, ktoré majú vo svojom `docker-compose` vlastnú **migračnú službu**, si to spustia samy pri štarte. Ukázalo sa ale, že aplikácia, ktorá takú službu nemá (napr. NEX Shopify), sa po nasadení spustila proti **nepripravenej databáze** — chýbajúca tabuľka spôsobila chyby, a migráciu bolo treba dobehnúť ručne.

Od tejto verzie:

- **Nasadzovač po štarte sám dobehne migrácie** — ak aplikácia používa Alembic a nemá vlastnú migračnú službu, nasadzovač spustí `alembic upgrade head` v jej backend kontajneri. Operácia je bezpečná na opakovanie (keď je databáza už aktuálna, nič nespraví).
- **Aplikácie s vlastnou migračnou službou** ostávajú bez zmeny — migráciu si spustia samy pri štarte, nasadzovač ju už druhýkrát nespúšťa.
- **Neúspešná migrácia = neúspešné nasadenie** — ak sa migrácia nepodarí, nasadenie skončí chybou (rozbitá schéma databázy nie je „úspech"), namiesto tichého spustenia proti chybnej databáze.

Platí rovnako pre UAT aj PROD. Vďaka tomu sa každá databázová aplikácia z NEX Studia nasadí s pripravenou databázou už na prvý pokus — bez ručného zásahu.

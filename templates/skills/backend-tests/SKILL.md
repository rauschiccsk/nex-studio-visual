---
name: backend-tests
description: Ako spúšťať skúšky backendu projektu — proti reálnemu PostgreSQL, v izolovanom Programovaní cez .venv a DATABASE_URL od enginu, v CI cez Dockerfile.test. Použi pred prvým spustením pytest v ťahu, keď prostredie skúšok zlyhá (pytest alebo alembic sa nenájde, Read-only file system, CalledProcessError v conftest.py) a pri zmene conftest.py, Dockerfile.test alebo služby test v docker-compose.yml.
---

# Skúšky backendu

## Na čom bežia (v4.0.53)

Skúšky backendu bežia proti **reálnemu PostgreSQL, nikdy nie SQLite**. Aplikácie používajú SQL, ktoré vie len
Postgres (`RETURNING`, `unaccent`/`immutable_unaccent`, GIN indexy `pg_trgm`) — SQLite v pamäti sa ticho
rozíde a CI sčervenie pri prvej zmene backendu (SQLite < 3.35 nevie `RETURNING`).

- **Jeden zdieľaný `conftest.py`** (žiadny vlastný sqlite engine v module) postaví schému cez
  `alembic upgrade head` proti Postgresu a skúšky od seba oddelí cez `TRUNCATE`; `client` používa
  `https://testserver`, aby prešla zabezpečená cookie s `Secure`.
- **Obraz skúšok je `Dockerfile.test` v koreni repozitára** (kontext `.`, `pip install -e ".[dev]"` — editable,
  aby `import app` bol zdroj aj s dátovými súbormi, nie balík bez nich; `COPY backend/... ./` + `COPY docs /docs`).
  Spúšťa ho služba **`test`** v compose na sieti s `db` cez `docker compose run --rm --build test` — to je cesta
  pre **CI**, nie pre izolované Programovanie.
- **Self-hosted runner s Dockerom v Dockeri:** pripojený priečinok (`volumes: ./docs`) daemon nevidí (príde
  prázdny). Súbory, ktoré skúška potrebuje (napr. archív dokumentov pre skúšku rozdielu), musia ísť do obrazu
  cez **`COPY`** — kontext stavby sa daemonu posiela.
- **Prah nikdy neznižuj** — nevypínaj skúšky, nedvíhaj verziu SQLite. Skúšaj na tom, na čom aplikácia beží.

## V izolovanom Programovaní (ICCINT-16)

- **Databázu ti dá engine.** Pred každým ťahom Programovania naštartuje čerstvý PostgreSQL a jeho adresu vloží
  do premennej **`DATABASE_URL`**. Databáza je **prázdna** a po ťahu **zaniká** — schému si postav sám
  (`alembic upgrade head`, presne ako `conftest.py`) a na dáta z minulého ťahu sa nespoliehaj.
- **Prostredie skúšok si postav do `.venv` — `pip install --user` v izolovanom priestore ZLYHÁ.** Obraz
  izolovaného priestoru nesie závislosti NEX Studia, **nie tvojho projektu**: `pytest` nie je na `PATH` a
  `asyncpg` ani `pyjwt` (`import jwt` v `conftest.py`) v ňom nie sú. `pip install --user` skončí na
  `Read-only file system`, lebo `$HOME/.local` je pripojený len na čítanie — **nie je to porucha a nie je to
  `framework_issue`**, len tam tá cesta nevedie. Overený postup (raz za ťah; `.venv/` je v `.gitignore`, takže
  sa nikdy nezakomituje, a medzi ťahmi v projekte prežije):

  ```bash
  cd backend
  python3 -m venv .venv                  # ak .venv už je z minulého ťahu, tento a ďalší riadok preskoč
  .venv/bin/pip install -e ".[dev]"      # ~20 s
  export PATH="$PWD/.venv/bin:$PATH"     # POVINNÉ — pozri nižšie
  alembic upgrade head                   # schéma do DATABASE_URL, presne ako conftest.py
  pytest -q
  ```

- **`export PATH` nevynechaj.** Nestačí volať `.venv/bin/pytest` — `conftest.py` si sám púšťa
  `subprocess.run(["alembic", "upgrade", "head"])`, teda hľadá `alembic` na `PATH`; bez toho exportu padne
  **každá** skúška na `subprocess.CalledProcessError` (overené: 100 chýb → po exporte 99 prešlo, 1 padla z iného
  dôvodu).
- Skúšky backendu tu spúšťaj **takto, priamo proti `DATABASE_URL`** — **nie** cez `docker compose run --rm test`
  (Docker v izolovanom priestore nie je).

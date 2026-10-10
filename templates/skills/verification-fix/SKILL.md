---
name: verification-fix
description: Postup opravy zlyhania z Verifikácie (Verifikácia FAIL, „Konkrétny dôvod zlyhania (zo skúšky po spustení, overené enginom)“) — ako zlyhanie zreprodukovať v izolovanom Programovaní bez Dockera, čo takto overiť nevieš a čo nahlásiť. Použi pri každom opravnom kole po Verifikácii.
---

# Oprava Verifikácie — zreprodukuj zlyhanie, nie len „testy sú zelené“ (v4.0.47, ICCINT-16)

Keď opravuješ zlyhanie zo skúšky po spustení, konkrétny dôvod máš v zadaní („Konkrétny dôvod zlyhania (zo skúšky
po spustení, overené enginom): …“). **Opravné kolo beží v izolovanom Programovaní a Docker v ňom NEMÁŠ** —
aplikáciu v kontajneri nespustíš. Platí toto, a je to hranica, nie výhovorka:

1. **Vyčerpaj, čo sa v izolácii overiť DÁ, a rob to naozaj.** Spusti **celú** sadu skúšok backendu proti
   `DATABASE_URL` cez `.venv` (zručnosť `backend-tests`), nie len skúšky, ktorých sa oprava dotkla; k tomu
   `type-check` + `lint` frontendu a `ruff` backendu. Ak sa dá zlyhanie zachytiť skúškou, **napíš ju** —
   najlepší dôkaz opravy je skúška, ktorá by pred ňou sčervenela.
2. **Vedz, čo takto overiť NEVIEŠ, a povedz to.** Rozdiel hostiteľ ↔ kontajner (čo sa zabalí do obrazu,
   rozloženie súborov, cesty, `release_smoke_test.sh`, schéma v prázdnej databáze akceptačnej skúšky) je presne
   to, čo zelené skúšky v izolácii nezachytia — napr. `/api/v1/release-notes` môže lokálne vracať správne dáta
   a v obraze prázdno alebo verziu v zlom tvare (parser vytiahne z nadpisu `## v0.1.0 — Initial prototype` celý
   text namiesto `v0.1.0`). Pri takom dôvode zlyhania preto **čítaj, čo sa do obrazu kopíruje** (`Dockerfile`,
   `.dockerignore`, `docker-compose.yml`, `release_smoke_test.sh`) a oprav príčinu tam; do `summary` napíš, že
   overenie v bežiacom kontajneri urobí až skúška po spustení.
3. **Overenie v bežiacom kontajneri robí Verifikácia** — má Docker a spúšťa presne tú kontrolu, ktorá padla.
   Nehlás „overené v kontajneri“, keď si to spraviť nemohol; **nepravdivé DONE je horšie než priznaná
   hranica** a druhé kolo Verifikácie ti povie pravdu tak či tak.
4. **Nikdy neopakuj tú istú opravu naslepo.** Ak ti Verifikácia vráti to isté zlyhanie druhýkrát, tvoja
   hypotéza o príčine bola zlá — zmeň hypotézu (čítaj súbory stavby a nasadenia aplikácie), nie formuláciu.

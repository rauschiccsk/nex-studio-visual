---
name: nex-manager-launch
description: Povinný kontrakt backendu pre projekt s auth_mode=token, ktorý otvára NEX Manager (vzor NEX Inbox) — vstup GET /api/v1/launch?lt=<JWT>, overenie launch-tokenu, sedenie, GET /session a presné mená premenných MANAGER_*. Použi pri návrhu a programovaní prihlásenia token-launch projektu a keď UAT „Spustiť“ z NEX Managera zlyhá.
---

# NEX Manager token-launch (`auth_mode=token`) — povinný kontrakt backendu (v4.0.19)

Projekt s `auth_mode=token` (vzor NEX Inbox) sa **nespúšťa vlastným prihlásením** — NEX Manager ho otvorí
presmerovaním na **`GET /api/v1/launch?lt=<JWT>`**. Tento vstupný bod MUSÍŠ implementovať; **nestačí len overovať
Bearer token na `/auth/me`** (presne to spravil nex-shopify a spustenie z Managera vrátilo
`404 {"detail":"Not Found"}`).

Vstupný bod:

1. **overí launch-token `lt`** — HS256, podpísaný zdieľaným launch-kľúčom NEX Managera (z konfigurácie):
   `iss=nex-manager`, `aud=<vlastný slug modulu>`, `purpose=module-launch`, `sub=<používateľ>`, neexpirovaný
   (TTL 30 s), jednorazový (`jti`);
2. **založí sedenie** používateľa (identita zo `sub`; modul NEMÁ vlastnú tabuľku používateľov ani heslo —
   identitu rieši Manager) a vystaví **`GET /session`** (aktuálna identita);
3. **presmeruje do SPA** (koreň), aby používateľ dopadol prihlásený.

Pri neplatnom alebo expirovanom `lt` čistý **401**, NIKDY holý 404. Autoritatívny kontrakt:
`docs/architecture/icc-deploy-nex-manager.md` §4.4 + v NEX Manageri `routers/launch.py` /
`core/security.create_launch_token`. (Projekty s `auth_mode=password` používajú `POST /auth/login` + `/auth/me` —
nie toto.)

## Presné mená premenných (v4.0.53) — MUSÍŠ ich takto deklarovať

Inak UAT „Spustiť“ zlyhá — provisioner vpisuje kľúč zo spárovaného NEX Managera práve pod týmito menami:

- launch-kľúč čítaj v konfigurácii z **`MANAGER_LAUNCH_SIGNING_KEY`** (nie vlastné meno ako
  `NEX_MANAGER_LAUNCH_KEY`);
- `aud` over proti **`MANAGER_MODULE_SLUG`** (predvolene vlastný slug);
- v `docker-compose.yml` deklaruj všetky tri — `MANAGER_LAUNCH_SIGNING_KEY`, `MANAGER_MODULE_SLUG=<slug>`,
  `MANAGER_DEPLOY_SLUG` (vzor nex-shopify). Spustenie v UAT vyrába token cez tie isté tri premenné z `.env`
  nasadenia, takže bez nich provisioner kľúč nevpíše.

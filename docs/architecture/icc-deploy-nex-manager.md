# ICC Deploy Architektúra — NEX Genesis model (token unifikácia)

**Status:** NÁVRH na schválenie (waterfall — toto je plán pred implementáciou)
**Autor:** Lead architect (Dedo, NEX Studio)
**Pre:** Zoltán (Director)
**Dátum:** 2026-06-20
**Rozhodnutie Directora:** prijať NEX Genesis model (NEX.exe, 30+ rokov overený) pre nové ICC prostredie.

---

## 1. Kontext a cieľ

### 1.1 Rozhodnutie

Prijímame **NEX Genesis model** pre ICC: jeden hlavný program na Deploy = **launcher/manager** podriadených aplikácií (modulov). Iba manager sa prihlasuje menom+heslom; moduly sa spúšťajú **tokenom**. Jeden register používateľov na Deploy = jediný zdroj pravdy.

### 1.2 Čo to znamená v ICC pojmoch

Zavádzame dva nové, navzájom kolmé (orthogonal) koncepty, ktoré v kóde dnes **neexistujú**:

1. **`archetype`** na projekte — voľba pri zakladaní projektu, **nemenná po vytvorení**, ktorá deterministicky riadi auth metódu, prítomnosť registra používateľov, tvar compose/siete, UAT cieľ a charters Designera/Auditora. Je kolmá na existujúce `category` (singlemodule/multimodule) — nepreťažujeme `category`.
2. **Deploy** — nová provisioning jednotka vyššieho rádu: jeden NEX Manager + N modulov zdieľajúcich JEDNO prostredie, JEDEN compose, JEDEN register používateľov (Managerov), JEDNU verejnú doménu.

### 1.3 Princíp jednotného modelu (mentálny model)

**Všetko čo sa provisionuje je Deploy.** Samostatný projekt = degenerovaný "Deploy o jednom člene" s vlastným loginom. NEX Manager + moduly = viacčlenný Deploy s token-launch pre moduly. Tým získavame jeden čistý mentálny model namiesto dvoch nesúrodých.

### 1.4 Kľúčový poznatok z groundingu — a jeho úprimný rozsah

**nex-inbox je dnes ~95 % type-A modul** (token-launch, nula user tabuliek) — to je overené a je to základ celého návrhu. **ALE: "meníme iba issuance leg, všetko ostatné carry-over unchanged" je nepresné a opravujem to teraz.** Reálny inbox `/launch` route (`/opt/projects/nex-inbox/backend/apps/auth_gate/router.py:33-85`) je `GET /api/v1/launch?pid=&user=&token=` — **tri** query parametre, `user` v čistom texte, `pid` **povinné** (`SessionData.pid` je `min_length=1`). Náš kontrakt (§4.4) je `GET /launch?lt=<JWT>` — **iný route signature**. Navyše meníme `SessionData` schému (drop `pid`, `tenant`→`deploy`+`module` — obe dnes mandatory, `schemas.py:11-17`) a meníme error namespace `NIB-080/081` → `NXM-*`.

Preto: modul-side migrácia je **reálny auth-gate refactor inboxu**, nie one-line swap. Konkrétny rozsah delty je úprimne vymenovaný v §4.6 a §7, aby Implementer nepodcenil scope a Auditor neflagol drift. Durable carry-over (FE `ProtectedRoute`/`LaunchRequired`, `current_session` dependency tvar, sliding-window re-emit, audit log volania) **ostáva** — ale session JWT payload, route signature, cookie meno, `Secure` default a error kódy sa **menia**, a to explicitne hovoríme.

### 1.5 Tvrdé obmedzenia (nemenné)

- **Presne DVE login metódy navždy:** meno+heslo (vzor NEX Studio) ALEBO token-launch (vzor NEX Inbox). **NIKDY email-as-login. NIKDY vymyslieť tretiu metódu.**
- **Štandardný iniciálny admin:** username `admin`, password `Nex123`, `must_change_password` pri prvom prihlásení.
- **NEX Studio ostáva nezávislé** (vlastní users, meno+heslo) — je to dev cockpit, NIE súčasť žiadneho Deploy. Nikdy sa neprovisionuje ako člen Deploy.

---

## 2. Dva archetypy projektu

Voľba pri zakladaní projektu. Kolmá na `category`. Nemenná po vytvorení.

| Aspekt | **A) `nex_module`** | **B) `standalone`** |
|---|---|---|
| Login metóda | token-launch (vzor NEX Inbox) | meno+heslo (vzor NEX Studio) |
| Vlastný register používateľov | NIE — identitu rieši z NEX Managera | ÁNO — vlastné `users`/`user_sessions` |
| Seed admin | NIE (Manager seedne raz na Deploy) | ÁNO — `admin`/`Nex123`/`must_change_password` |
| Login page | `LaunchRequiredPage` (žiadny LoginPage) | `LoginPage` + `LoginForm` |
| BE auth kontrakt | `GET /api/v1/launch?lt=` + `/session` | `POST /auth/login` + `/auth/me` + `/auth/logout` + `/auth/change-password` |
| UAT cieľ | pripojí sa do Deploy UAT `/opt/uat/<deploy_slug>/` | vlastný UAT `/opt/uat/<uat_slug>/` (dnešný model) |
| Deploy | člen Deploy | žiadny Deploy (degenerovaný Deploy o jednom) |

### 2.1 Rozhodnutie: dvojhodnotový archetype, NEX Manager je `standalone`

**Odporúčam dvojhodnotový model `archetype ∈ {nex_module, standalone}`** — NEX Manager NIE JE samostatný tretí archetype, ale **najprísnejší prípad `standalone`**: prihlasuje sa menom+heslom (vzor Studio), vlastní register, sídli vo vlastnom projekte. Jeho jedinečnosť (mintuje launch tokeny, kotví Deploy) je daná tým, že je `manager_project_id` v tabuľke `deploys` — NIE novou hodnotou archetypu.

Zdôvodnenie podľa 4 kritérií:
- **Kvalita/dlhodobosť:** archetype je čistá os "akú auth metódu a register má projekt". `nex_manager` zdieľa odpoveď so `standalone` (login + vlastný register), líši sa len rolou v Deploy — čo je správne vyjadrené vzťahom v `deploys`, nie tretím archetypom. Tretia hodnota by replikovala "som issuer" do dvoch miest (archetype + deploys.manager_project_id).
- **Praktickosť:** scaffolding `nex_manager` = scaffolding `standalone` + extra token-mint vrstva (postavená v P1). Dva archetypy = dve scaffold vetvy namiesto troch.
- **Profesionálnosť:** jedna os, jeden zdroj pravdy pre "kto je manager Deployu" = `deploys` tabuľka.

Trojhodnotový variant `{nex_manager, nex_module, standalone}` je **legitímna alternatíva, nie len horšia** — preto ju neuvádzam ako default, ale **explicitne ju predkladám v §8 (Otvorené rozhodnutie 1)**, lebo materiálne mení data model (CHECK constraint migrácie 069 + počet scaffold vetiev).

### 2.2 Default = `standalone`, s explicitným backfill pre nex-inbox

`standalone` je striktne bezpečnejší **scaffold** default: projekt, ktorý by omylom dostal `nex_module`, by sa scaffoldol BEZ auth a BEZ registra (nespustiteľný izolovane); opačná chyba len pridá login, ktorý operátor ignoruje.

**ALE pozor na backfill napätie** (review #12): `server_default='standalone'` by ticho mis-klasifikoval **nex-inbox**, ktorý je nesporne type-A. `standalone` nex-inbox by Auditor FAILol (nemá `/auth/login`, nemá users tabuľku). Riešenie: **migrácia 069 má data step, ktorý explicitne nastaví `archetype='nex_module'` pre nex-inbox** (jediná známa type-A appka), zvyšok ostáva `standalone`. Tým je default bezpečný pre budúce projekty a backfill správny pre realitu. nex-ledger/nex-asistent ostávajú `standalone` až do explicitnej konverzie (Rozhodnutie 2) — Auditor archetype-checky sa na ne nespúšťajú, kým nie sú konvertované.

---

## 3. NEX Manager (nex-manager) — appka

Centrálny auth + register používateľov + register modulov + issuer tokenov pre jeden ICC **Deploy**. Postavený ako `standalone` projekt (je tá jediná aplikácia, ktorá sa prihlasuje menom+heslom a kotví Deploy). Postaví ho NEX Studio ako normálny projekt.

### 3.1 Strategické umiestnenie

- NEX Manager = manager appka Deployu. JEDEN na Deploy. Vlastní jediný register používateľov. Mintuje launch tokeny. Riadi ktoré moduly smú spúšťať a ktorí používatelia smú spustiť ktorý modul.
- Modul (archetype A) NEMÁ login page, NEMÁ user tabuľku, NEMÁ heslo. Vystavuje len `GET /launch?lt=` a plnú identitu rieši z Managera.
- NEX Manager **stelesňuje šev medzi dvoma login metódami:** konzumuje metódu 1 (meno+heslo) pre ľudí a mintuje metódu 2 (token-launch) pre moduly. Žiadna tretia metóda, nikdy email-as-login.

### 3.2 Data model — 5 tabuliek

`users` + `user_sessions` sú prevzaté z vzoru NEX Studio (`backend/db/models/foundation.py:19-60`) s deltami. `modules`, `module_grants`, `launch_audit` sú net-new.

**`users`** (vzor Studio `User`, delty):
- `id` UUID PK, `username` String(50) UNIQUE NOT NULL (login key — nikdy email), `email` String(255) UNIQUE NOT NULL (atribút, NIE login key), `password_hash` String(255) (bcrypt rounds=12).
- `role` String(10) CHECK `role IN ('admin','user')` — **delta** od Studio `ri/ha/shu` (overené: `foundation.py:39-42`). Dvojrolový model `admin | user` verne zachytáva Directorov model "admin spravuje používateľov+moduly vs. ostatní len spúšťajú". `admin` ≙ Studio `ri`. Zachovávame enforcement *pattern* (DB CHECK + `Literal` + `require_*` dependencies), mení sa len rola *set*.
- `is_active` Boolean default true, `first_name`/`last_name` String(100) NULL.
- **`must_change_password` Boolean default true — net-new** (gap, ktorý grounding našiel chýbajúci vo vzore Studio: nie je na modeli `foundation.py:19-43`, ani na schéme/seede; FE hook `validateAfterLogin` existuje na `nex-shared/src/auth-store.ts:50`, ale je nevyužitý — NEX Manager je jeho prvý konzument).
- `telegram_chat_id` **vypustený** (vo Studio slúži na agent-notify ownership, v Deploy bez významu).

**`user_sessions`** (vzor Studio, verbatim): `user_id` (CASCADE), `token_version` (default 0), `last_seen_at`. JWT kill-switch backing. **Tento `token_version` je centrálny revocation epoch pre celý Deploy** — gatuje Managerovu vlastnú JWT, issuance launch tokenov (§4.3) AJ live module sessions (§4.5 mechanizmus revokácie).

**`modules`** (net-new — register modulov):
- `id` UUID PK, `slug` String(50) UNIQUE (slug projektu modulu), `display_name` String(120), `description` String(500) NULL, `base_url` String(255) (kam poslať browser pre launch), **`launch_enabled` Boolean default true** (globálny kill-switch per modul — Directorovo "control which modules can launch"), `sort_order` Integer default 0.
- **`identity_key_hash` String(255) NOT NULL — net-new** (review #4): per-module secret hash pre `/identity` service-to-service auth. Manager generuje per-module identity key pri registrácii modulu; modul ho drží v `.env`. Tým nie je `/identity` auth viazaný na zdieľaný launch kľúč (viď §4.5 fix).

**`module_grants`** (net-new — per-user × per-module launch oprávnenie):
- `id` UUID PK, `user_id` FK→users (CASCADE, indexed), `module_id` FK→modules (CASCADE, indexed), `granted_by` FK→users (SET NULL), `created_at`. UNIQUE(`user_id`,`module_id`).
- Absencia riadku = nepovolené (deny-by-default).

**`launch_audit`** (net-new — telemetria launchov):
- `id`, `user_id` FK (SET NULL), `module_id` FK (SET NULL), `result` String(20) CHECK `IN ('issued','denied_disabled','denied_no_grant','denied_inactive','denied_pw_change')`, `client_host` String(64) NULL, `created_at`.

### 3.3 Dvojbránová launch autorizácia (rozhodnutie)

Launch je povolený **iff `module.launch_enabled` AND existuje `module_grants` riadok pre (user, module)**. Module-level switch = adminove "vypni celý modul pre všetkých"; grant = "zapni ho pre tohto konkrétneho používateľa". Minimálny model spĺňajúci obe Directorove požiadavky bez vymýšľania tretieho konceptu. **`admin` rola NEOBCHÁDZA grants** — aj admin potrebuje grant (drží launch path uniformný a auditovateľný; admin si grantne sám cez UI).

### 3.4 Auth (ľudia) — vzor Studio prevzatý + server-side password-change enforcement

Endpointy pod `/api/v1`:

| Endpoint | Vzor | Pozn. |
|---|---|---|
| `POST /auth/login` | `nex-studio routes/auth.py` | `{username,password}` → `{access_token, token_type, expires_in, user: AuthUser}`. Generic 401. |
| `POST /auth/logout` | Studio | 204, bumpne `token_version`. |
| `GET /auth/me` | Studio | → `AuthUser`. |
| `POST /auth/change-password` | nový thin wrapper | self-service; pri úspechu vyčistí `must_change_password`, bumpne `token_version`. |

- `AuthUser` (vzor `schemas/auth.py`) získava jedno pole: `must_change_password: bool`.
- JWT = HS256, payload `{sub, role, tv, exp}`, secret per-Deploy. `tv` validovaný proti DB na každom requeste.
- RBAC: `require_admin_role` (rename `require_ri_role`, role check `== 'admin'`). Všetky user-mgmt + module-mgmt + grant endpointy gated `require_admin_role`. `change-password` je self-or-admin.

**Server-side forced-password-change gate (review #6 — kritický fix):** `must_change_password` NESMIE byť len FE gate (`validateAfterLogin` je iba frontend hook s nula konzumentmi). Pridávame **BE dependency `require_password_changed`** na VŠETKY Manager endpointy okrem `/auth/me`, `/auth/logout`, `/auth/change-password`. Používateľ s `must_change_password=true`, ktorý zavolá API priamo (mimo SPA), dostane **`403 NXM-PW-CHANGE-REQUIRED`** na admin/module/grant/launch endpointoch. FE gate je UX; BE dependency je control. Bez tohto by admin onboardnutý s `Nex123` mohol cez API hitnúť `/admin/users` pod default heslom.

### 3.5 UI plochy (FE) — 5 stránok

Shared `nex-shared` chrome + `createAuthStore({mode:"login"})`.
- **`/login`** — `<LoginForm fieldLabel="username">`. Žiadne email pole nikde.
- **`/change-password`** (forced first-login) — dosiahnuteľné keď `must_change_password`; FE blokuje ostatné routes cez `validateAfterLogin`, BE ich blokuje cez `require_password_changed` (§3.4). Oba gaty, lebo FE sám nestačí.
- **`/`** — Launcher (domov pre každého používateľa): tiles z `GET /api/v1/me/modules`. Launchable modul = tile s Launch tlačidlom → `POST /api/v1/launch/{slug}` → `window.open(launch_url)`. Non-launchable moduly = **disabled s tooltipom, nikdy skryté** (ICC pravidlo "disabled state over hidden").
- **`/admin/users`** (admin only) — Studio SettingsPage user-mgmt kit; role selector limit `admin | user`; self-guards (nemôžeš deaktivovať/zmazať vlastný účet).
- **`/admin/modules`** (admin only) — net-new: module list s `launch_enabled` toggle, register module (vygeneruje identity key), grants matrix (per modul zoznam používateľov s grantom).

### 3.6 Migrácie

Standalone projekt → vlastná Alembic reťaz: `001_initial_schema` (users + user_sessions + CHECK + must_change_password), `002_modules_and_grants` (vrátane `identity_key_hash`), `003_seed_admin` (§5).

---

## 4. Token-launch kontrakt (NEX Manager ↔ Modul) — v1

Generalizácia vzoru nex-inbox `auth_gate/*` na štandardný znovupoužiteľný kontrakt. Rozsah delty je v §1.4 a §4.6 — **nie one-line swap**, ale durable carry-over (FE pattern, audit volania, `current_session` tvar) ostáva.

### 4.1 Tri toky + tri trust roots

Toky: (1) issuance (Manager → browser), (2) redemption + session (modul overí token, mintne vlastnú session cookie), (3) identity resolution (modul fetchne plnú user data z Managera).

| Kľúč | Scope | Držitelia | Účel |
|---|---|---|---|
| `LAUNCH_SIGNING_KEY` | per **Deploy** | Manager + VŠETKY moduly (shared) | sign/verify launch tokeny |
| `SESSION_SIGNING_KEY` | per **modul** | iba ten modul (private) | sign/verify vlastnú session cookie |
| `IDENTITY_KEY` | per **modul** | iba ten modul + Manager (per-module hash v `modules.identity_key_hash`) | autentikuje modul `/identity` callu (review #4 fix) |
| Manager `secret_key` | iba Manager | Manager | Managerova vlastná SPA session JWT |

**Trust izolácia (review #4 — kritický fix):** pôvodný návrh autentikoval `/identity` call zdieľaným `LAUNCH_SIGNING_KEY` — čo znamená, že KTORÝKOĽVEK modul (drží zdieľaný kľúč) by mohol mintnúť `module-identity` token impersonujúci ANY iný modul a vyžrať name/email/role všetkých používateľov Deployu. To rušíme. Každý modul má **vlastný `IDENTITY_KEY`** (hash registrovaný v Manageri). Kompromitácia jedného modulu = blast radius **len ten modul**, Manager vie atribútovať a revokovať jednotlivý modul. Plus `/identity` binduje výlučne na internú Deploy sieť (nikdy cez Traefik). Dva nezávislé obranné prvky: per-module kľúč + network izolácia.

### 4.2 Issuance (Manager side)

Token sa mintuje **per launch, za behu, nikdy pri onboarding** (jadro delty vs. inbox statický `.env` token).

`POST /api/v1/launch/{module_slug}` (auth: `get_current_user` + `require_password_changed`):
1. Resolve module → 404 ak chýba.
2. Dvojbránová kontrola (§3.3): `launch_enabled` AND grant. Inak 403 + audit `denied_disabled`/`denied_no_grant`.
3. `is_active` inak 403 (`denied_inactive`).
4. **must_change_password gate:** dependency `require_password_changed` už odmietol 409 (`NXM-PW-CHANGE-REQUIRED`) pred handlerom; audit `denied_pw_change`. Belt-and-suspenders s §3.4.
5. Mintni launch JWT (§4.3, `tv` = aktuálny `token_version`). Audit `issued`.
6. Vráť `{ launch_url, expires_in: 30 }`.

### 4.3 Launch token claims (podpísaný JWT, HS256)

```jsonc
{
  "iss": "nex-manager",          // fixný issuer
  "aud": "ledger",               // module_slug — token viazaný na JEDEN modul
  "sub": "jdoe",                 // user LOGIN NAME (registry key)
  "deploy": "mager-uat",         // Deploy slug — tenant/registry scope
  "tv": 7,                       // token_version snapshot — central revocation
  "jti": "9f2c…",                // unique token id (UUID4) — replay protection
  "purpose": "module-launch",    // typ guard
  "iat": 1718900000,
  "exp": 1718900030              // iat + 30s — short fuse
}
```

Minimalistický: nesie **len login name** (`sub`) + revocation epoch (`tv`), nikdy name/email/role — tie sa riešia naživo z Managera (§4.5). Stale profile data nikdy necestujú v URL; PII nie je v browser history / nginx logoch.

**Podpis/expiry/replay:**
- HS256 s `LAUNCH_SIGNING_KEY` (≥256-bit, generovaný raz per Deploy provisionerom, injektovaný do Managera AJ každého modulu). HS256 (nie RS256) zvolené, lebo Manager + moduly sú co-located v jednom izolovanom Deploy provisionovanom NEX Studiom — zdieľaný symetrický secret je jednoduchší a zero-maintenance (princíp full-autonomy). Ak by Deploy niekedy preklenul untrusted hranice, tento leg sa vymení na RS256 (mení sa len verify primitive).
- `exp = iat + 30s` (one-shot hand-off). Modul odmietne `exp` v minulosti **a** tokeny s `exp - iat > 60s` (hard cap proti misconfigured/forged long-lived tokenu).
- `jti` (UUID4) **single-use** (§4.4 store + eviction).
- `aud` binding: token pre `ledger` odmietne `asistent`.

### 4.4 Redemption + session (Module side)

`GET /api/v1/launch?lt=<JWT>` (no-auth route — **iný signature než inbox 3-param `?pid=&user=&token=`**, viď §1.4). Verifikácia v poradí:
1. `lt` prítomný → inak `NXM-LT-MISSING` (400).
2. Podpis verifikuje proti `LAUNCH_SIGNING_KEY` (constant-time) → inak `NXM-LT-INVALID` (401) + audit.
3. Claims well-formed: `iss=="nex-manager"`, `purpose=="module-launch"`, `aud==MODULE_SLUG`, `deploy==DEPLOY_SLUG`, `sub` non-empty, `jti` prítomný → inak `NXM-LT-INVALID`.
4. Expiry: `exp` nie v minulosti AND `exp-iat<=60s` → inak `NXM-LT-EXPIRED` (401).
5. **Single-use, fail-closed (review #5):** atomicky claim `jti` cez `INSERT … ON CONFLICT DO NOTHING` do `consumed_launch_tokens`. Už claimed → `NXM-LT-REPLAYED` (409) + audit. **Ak INSERT nemožno potvrdiť (DB error), vráť `NXM-LT-INVALID` — NIKDY neadmitni launch (fail-closed).** Replay protection sa nikdy ticho nevypne pod DB tlakom.

**`consumed_launch_tokens` eviction (review #5):** tokeny žijú 30s, takže riadok starší než ~60s je mŕtvy. Tabuľka: `jti` PK, `exp` (indexed), `created_at`. Prune-on-insert: každý INSERT zmaže `WHERE exp < now() - interval '5 min'`. Tým tabuľka ostáva malá bez sweeper cronu (zero-maintenance).

Pri úspechu modul mintne **vlastnú session JWT**: HS256 s module-private `SESSION_SIGNING_KEY`, `302` na SPA root + `Set-Cookie`:
- Cookie meno štandardizované na **`nex_session`** (drop app-specific `nex_inbox_session`).
- `HttpOnly`, `SameSite=Strict`.
- **`Secure` config-driven, NIE hardcoded (review #3):** zostáva env var `SESSION_COOKIE_SECURE`, **defaulted `True`** v Deploy template (TLS je Deploy kontrakt za Traefik), ale overridable. Inbox dnes `Secure=False` pre Tailscale plain-HTTP prístup (`settings.py:52`) — hardcoded `True` by spravil cookie nedoručiteľnú cez plain HTTP (priamy container hit, internal access). Default `True` + override, nie hardcode.
- `Max-Age=28800` (8h, env `SESSION_TTL_SECONDS`).

**SessionData schema delta (review #2 — úprimný rozsah):** inbox `SessionData` (`schemas.py:11-17`) má `user`, `pid` (mandatory), `tenant` (mandatory), `iat`. Náš modul: **drop `pid`** (NEX-Genesis-specific; ak Deploy potrebuje launch correlator, cestuje ako optional `launch_ref`), **`tenant` → `deploy` + `module`**. Toto je schema change, nie carry-over — Implementer to musí vedieť, inak DONE report podcení scope.

Per-request `current_session` dependency: decode + deploy+module pin + sliding-window re-emit (fresh `iat`, 8h) + **central revocation check (§4.5)**. Len `/health` a `/launch` bypassujú dependency.

### 4.5 Identity resolution + central revocation (Module → Manager)

Net-new leg, ktorý vzor inbox zámerne nemá. Session nesie len login name; plná user data sa riešia **naživo z Managera, nikdy cachované v module user tabuľke**.

`GET /api/v1/identity/{login}` na NEX Manager:
- Response 200: `{login, first_name, last_name, email, role, is_active, must_change_password, token_version}`. **Nikdy `password_hash`.**
- 404 `NXM-USER-NOT-FOUND`, 403 `NXM-USER-INACTIVE`.

**Rozhodnutie: resolve cez API call, NIE cez claims.** Launched session žije až 8h; ak by name/email/role boli v claims, revokácia role/deaktivácia počas okna by bola pre modul neviditeľná. Live `/identity` call odráža aktuálny stav. **Claims nesú identity *pointer* (login); API nesie identity *data*.**

**Central revocation cez `/identity` (review #11 — kritický fix):** pôvodný návrh tvrdil, že `token_version` bump pri logoute zabije nevyplatené launch tokeny — to je takmer bezcenné (launch token žije 30s tak či tak), a **logout v Manageri by NIKDY neukončil už-launchnutú 8h module session.** Riešenie: `/identity` vracia aj `token_version`, a launch token nesie `tv` snapshot (§4.3). Module-side `current_session` dependency (ktorá `/identity` call už robí per-request) **porovná session `tv` proti live `token_version`**: ak Manager bumpol (logout / deaktivácia / password change), nezhoda → `401 NXM-SESSION-REVOKED`. Tým je revokácia skutočne centrálna, latencia = "ďalší request", a re-používa call, ktorý už beží (nula extra cost). Deactivate aj logout teraz ukončia live module session.

**Service-to-service auth (review #4 fix):** modul volá Manager cez **private Deploy network**, autentikovaný **per-module `IDENTITY_KEY`** (JWT podpísaný module's own identity key, `purpose:"module-identity"`, `aud:"nex-manager"`, `sub:<module_slug>`, short `exp`). Manager overí proti `modules.identity_key_hash` pre daný `sub` slug — tým vie atribútovať a revokovať jednotlivý modul. `/identity` **nikdy** cez Traefik — binduje len na internú sieť (defense-in-depth: aj keby identity key unikol, endpoint je nedosiahnuteľný zvonka).

### 4.6 Module side delta (do archetype-A scaffold) — úprimný rozsah

Modul = inbox `auth_gate` s **týmito konkrétnymi zmenami** (NIE one-line swap, review #2):
- **(a) Route signature:** `?pid=&user=&token=` → `?lt=<JWT>`.
- **(b) Verifikácia:** lokálny `secrets.compare_digest` proti statickému `LAUNCH_TOKEN` (`service.py:16-23`) → JWT signature+claims+expiry+single-use verify (§4.4) + per-module identity-key call.
- **(c) `SessionData` schema:** drop `pid`, `tenant`→`deploy`+`module` (§4.4).
- **(d) Identity-resolution leg:** net-new `/identity` httpx call + central revocation check (§4.5).
- **(e) Cookie:** meno `nex_inbox_session`→`nex_session`, `Secure` config-driven default `True`.
- **(f) Error namespace:** `NIB-080/081/082/083` → `NXM-*`.
- **(g) `.env`:** drop `LAUNCH_TOKEN` + `TENANT_SLUG`; add `MANAGER_BASE_URL` + `MODULE_SLUG` + `LAUNCH_SIGNING_KEY` + `SESSION_SIGNING_KEY` + `IDENTITY_KEY` + `DEPLOY_SLUG` + `SESSION_TTL_SECONDS`.

**Durable carry-over (skutočne unchanged):** FE `ProtectedRoute`/`LaunchRequired`, `current_session` dependency tvar (sliding-window re-emit), `/session` endpoint, audit log volania, JWT encode/decode primitíva. Toto je reálny auth-gate refactor inboxu — Auditor to nesmie flagnúť ako drift, lebo je to dokumentované tu.

### 4.7 End-to-end sekvencia

```
Human → Manager FE: login (meno+heslo)                     [Studio vzor]
Manager FE → klik "Launch NEX Inbox"
Manager FE → POST /api/v1/launch/nex-inbox (bearer JWT)
Manager BE: require_password_changed → gate(enabled ∧ grant ∧ active) → mint 30s launch JWT(tv) → audit
Manager BE → FE: { launch_url }
Manager FE: window.open(launch_url)
Browser → Module: GET /api/v1/launch?lt=<JWT>              [generalizovaný inbox route, ?lt=]
Module BE: verify sig+exp(≤60s)+aud+deploy+jti(single-use,fail-closed)
Module BE → Manager BE: GET /identity/{login} (per-module IDENTITY_KEY, private net)
Manager BE: → resolved identity + token_version
Module BE: build_session(deploy,module,tv) → Set-Cookie nex_session 8h → 302 "/"
Module FE: ProtectedRoute probes /session → authenticated   [inbox vzor]
[每 request] current_session → /identity → tv match? else 401 NXM-SESSION-REVOKED  [central revoke]
```

### 4.8 Kanonické error kódy (`NXM-*` namespace)

Module-side: `NXM-LT-MISSING` (400), `NXM-LT-INVALID` (401), `NXM-LT-EXPIRED` (401), `NXM-LT-REPLAYED` (409), `NXM-SESSION-EXPIRED` (401), `NXM-SESSION-INVALID` (401), `NXM-SESSION-REVOKED` (401), `NXM-USER-NOT-FOUND` (404), `NXM-USER-INACTIVE` (403).
Manager-side issuance: `NXM-ML-DISABLED` (403), `NXM-ML-FORBIDDEN` (403), `NXM-PW-CHANGE-REQUIRED` (409).

---

## 5. Deploy / UAT model

### 5.1 Deploy ako first-class jednotka

Dnes má codebase **presne jednu provisioning jednotku: projekt** (`uat_slug` 1:1 s `project.slug`, `derive_uat_slug` na `uat_provisioner.py:117`). Deploy je net-new jednotka vyššieho rádu.

**Nová tabuľka `deploys`:** `id`, `deploy_slug` UNIQUE (→ verejná doména), `manager_project_id` FK→projects NOT NULL, `created_at`, `created_by`.
**Nová tabuľka `deploy_members`:** `deploy_id` FK (CASCADE), `project_id` FK (RESTRICT — modul projekt), `launch_enabled` Boolean default true, `route_path` String. PK (`deploy_id`, `project_id`).

Standalone projekt nemá `deploys` riadok (cesta nezmenená). NEX Manager projekt dostane `deploys` riadok so sebou ako `manager_project_id`; každý modul = `deploy_members` riadok.

### 5.2 Provisioner — jedna služba, dva entrypointy

`uat_provisioner.py` má dnes `provision_uat` na `:708` (overené). Vystaví dve verejné funkcie zdieľajúce per-service transform helpery:

| | `provision_uat(project)` — archetype B | `provision_deploy(deploy)` — archetype A |
|---|---|---|
| Jednotka | jeden standalone projekt | jeden Deploy (Manager + enabled moduly) |
| UAT dir | `/opt/uat/<uat_slug>/` | `/opt/uat/<deploy_slug>/` |
| Source composes | 1 | N (Manager + každý enabled modul) |
| Service keys | `<svc>` | `<member>-<svc>` |
| User DB | vlastný | iba Managerov; identity DB modulov stripnutý |
| Auth | login (vlastní users) | Manager login + per-module token-launch |
| Doména | `uat-<slug>.isnex.eu` | `uat-<deploy>.isnex.eu` (moduly pod path) |
| Siete | private + nex-proxy-net | deploy-net + per-member-net + nex-proxy-net |
| Secrets | per-project synthetic | `LAUNCH_SIGNING_KEY` (shared) + per-module `SESSION_SIGNING_KEY` + per-module `IDENTITY_KEY` |

### 5.3 Archetype B (standalone) — dnešný model + bug fixy (vrátane external-net guard)

Žiadna architektonická zmena route/auth tvaru. Existujúca izolácia (`name: uat-<slug>`, strip explicit names na interných nets, len `nex-proxy-net` external) je už správna pre single-project.

**ALE: external-net guard platí pre OBA archetypy, nie len A (review #1 — kritická oprava kontradikcie).** Pôvodný text dával guard len pod §5.4 (archetype A), pričom §5.3 hovoril "B unchanged" — to si protirečí, lebo súčasný transform helper na `uat_provisioner.py:637-640` **zámerne zachováva external nety** (`if not net.get("external"): net.pop("name")`) a beží pre VŠETKY archetypy. nex-asistent aj nex-ledger sú dnes **standalone** a nex-asistent nesie `nex-network: external:true` (`docker-compose.yml:102-103`).

**Fix:** Guard je **globálny pre oba archetypy.** Allow-list external nets = `{nex-proxy-net}` (žiadny legitímny projekt nepotrebuje iný external net — Studio compose má nula external nets, ledger používa non-external `ledger-net`). Akýkoľvek iný external net = **hard provisioning error** (B aj A). Toto je bug fix, nie A-only feature. nex-asistent `nex-network: external:true` MUSÍ padnúť pri ďalšom redeploy bez ohľadu na archetype. §5.6 bod 4 to vynucuje aj v create-project template + Auditor charter.

### 5.4 Archetype A (nex_module) — Deploy-keyed provisioning (net-new vrstva)

`provision_deploy(deploy_slug)` renderuje JEDEN merged compose v `/opt/uat/<deploy_slug>/docker-compose.yml` (Managerov full stack + každého enabled modulu BE+FE).

**Service naming (collision-free):** dva moduly oba so službou `backend` skolidujú. Riešenie: service KEY = `<member>-<svc>` (`ledger-backend`, `manager-backend`), `container_name` = `uat-<deploy_slug>-<member>-<svc>`, image tag rovnako.

**Siete (izolácia):**
```
networks:
  deploy-<deploy_slug>-net:   interný shared bus (Manager alias + module BEs)   # NIE external
  <member>-net (per modul):   module FE↔BE private bridge                        # NIE external
  nex-proxy-net:              external: true   # JEDINÁ external sieť, len Traefik
```
- Každý modul má vlastnú private sieť `<member>-net` (FE→BE rieši pôvodný `backend` názov cez network alias).
- Manager vystaví stabilný alias `nex-manager` na `deploy-<deploy_slug>-net`, ku ktorej sa pripoja všetky module backendy. Moduly dosiahnu Manager (`/identity`) na `http://nex-manager:8000` — iba po internej sieti.
- **Žiadna služba sa nikdy nepripojí na external prod sieť.** Tu sa zablokuje nex-asistent `nex-network: external:true` leak na bráne (collision: module `postgres` → prod `nex-postgres`). Toto je štruktúrny fix DNS-collision + security leak buga.

**Single registry — deploy-wide table-name invariant (review #7 fix):** každý modul má v merged compose **DB service a DB-connection env stripnuté pre identity**. Existuje presne jeden Postgres-s-users v Deploy: Managerov. Modul MÔŽE mať module-private domain DB (ledger ho potrebuje), keyed `<member>-db` na `<member>-net`, plne private — ale **nikdy user/identity/auth store v ŽIADNEJ svojej DB**. Auditor pravidlo nie je "modul nemá identity DB", ale **"`nex_module` nemá tabuľku `users`/`user_sessions`/auth-tabuľku v ŽIADNEJ svojej DB" — keyed na názvy tabuliek, deploy-wide** (modul s private domain DB by inak mohol scaffoldnúť shadow `users` tabuľku pre non-auth účely). Archetype-A scaffold netemplatuje žiadnu user tabuľku.

**Traefik routing (path-prefix per modul):** jedna verejná doména `uat-<deploy_slug>.isnex.eu`.
- Manager FE — `Host(...)` catch-all, priority 10.
- Manager BE — `Host(...) && PathPrefix(/api)`, priority 20.
- Modul `m` FE — `PathPrefix(/m)`, priority 30, `stripprefix` middleware.
- Modul `m` BE — `PathPrefix(/m/api)`, priority 40, `stripprefix=/m`.

Path-prefix (nie subdomain-per-module): jeden wildcard cert, jeden DNS host, sedí mentálnemu modelu "launcher otvorí modul pod Managerom".

**Deploy-level teardown/redeploy:** pridanie/odobratie/enable/disable modulu **re-renderuje merged compose** + `up -d --build`. Redeploy zachová Deploy secrets + Manager DB volume + module domain-DB volumes (mirror CR-NS-061 redeploy-safe). `--rotate-secrets` forces fresh — **vrátane Deploy launch/session/identity kľúčov** (review #14): pri rotácii všetky module sessions invalidujú (acceptable, forces re-launch). Tým existuje rotation path pre leaknutý `LAUNCH_SIGNING_KEY` (najvyššej hodnoty kľúč — forges launch do každého modulu).

### 5.5 Engine routing seam

`_release_auto_uat_deploy` (`orchestrator.py:2732`, overené) sa vetví podľa `project.archetype`: `standalone` → `provision_uat` (dnes); modul → resolve Deploy z `deploy_members`/`deploys` → `provision_deploy(deploy_slug)` + Deploy-keyed `_run_uat_deploy`.

### 5.6 Známe bugy — konkrétne fixy (oba archetypy)

Tieto NIE sú archetype-specific; nový model ich MUSÍ niesť, inak moduly idú broken (lekcie nex-asistent):

1. **No-compose / no-FE-service = FAIL pre deployable archetypy, SKIP inak (review #13).** `_run_app_starts_smoke` SKIP (`orchestrator.py:2545-2549`) je dnes keyed čisto na `compose.is_file()` — žiadna archetype awareness. Flip-to-FAIL nesmie byť univerzálny (brand-new scaffold / library projekt legitímne nemá compose pri prvom smoke). **Gate FAIL na archetype: projekt, ktorého archetype implikuje deployable web app (`nex_module`/`standalone`) MUSÍ mať compose → FAIL ak chýba; inak SKIP.** Plus pridaj assertion že `frontend` service existuje a je reachable (dnes sa probuje len `backend` — pridaj `_compose_frontend_port` analóg). Toto chytá nex-asistent "no FE service emitted" bug.
2. **Post-`up` serve-verify.** `_run_uat_deploy` (`:2268`) dnes reportuje úspech na `up` exit 0 samotnom. Pridaj readiness poll cez Traefik (každé FE serves <500 AND každé BE `/api` odpovedá), reuse gate_g primitive. "exit 0" nie je "serves". Úspech sa reportuje LEN po overení; inak `blocked`, nie `awaiting_director`.
3. **IPv4 `127.0.0.1`, derived port.** K-004 scaffold smoke `curl http://localhost:8000/health` (`create_project_postscaffold.py:111`) → `127.0.0.1` + port z compose (`_compose_backend_port`). FE healthcheck rovnako `127.0.0.1` (nginx je IPv4; IPv6 `localhost` → false-unhealthy → Traefik drop FE → 404, nex-asistent bug).
4. **Private-net guarantee v template + Auditor.** Create-project template MUSÍ emitovať private nets + `nex-proxy-net` only; Auditor charter FAILuje na akýkoľvek external net ≠ `nex-proxy-net`. Štruktúrny fix DNS-collision/leak buga — vynútený pri generovaní + audite, nie len v provisioneri (§5.3 guard je posledná obrana; toto je prvá).

### 5.7 Deploy port allocation + DNS/Traefik (review #9 — nová sekcia)

Existujúci model derivuje per-project porty + `uat-<slug>.isnex.eu` cez host-nginx wildcard (v0.9.0 design). Deploy zavádza `uat-<deploy_slug>.isnex.eu` s path-prefix routingom pre N modulov:

- **Port allocation:** Deploy dostane **vlastný port range distinct od member-projektových UAT portov** (členovia môžu mať pred-konverziou aj vlastné standalone identity → port-collision risk). Deploy port range sa alokuje z Port Registry v2 ako jeden blok per Deploy, nie per-member. Interné service-to-service (`/identity`) nepotrebuje host port (private net only).
- **DNS/Traefik wildcard:** existujúci `uat-*` host-nginx wildcard pravidlo **pokrýva `uat-<deploy>` hostov pattern-om** (potvrdené: `uat-*` matchne aj deploy slug). Žiadna nová DNS práca pre Deploy.
- **No double-UAT:** member projekt v Deploy **NESMIE** zároveň self-provisionovať vlastný `uat-<member>` host (inak dva live UATy pre rovnaký kód). Engine seam (§5.5): ak projekt má `deploy_members` riadok, `provision_uat` sa NEspustí — len `provision_deploy`.

---

## 6. Zmeny v NEX Studio + fázovaný roadmap

### 6.1 Slot-iny (súbor → zmena)

- `backend/db/models/projects.py` — `archetype` column + `ck_projects_archetype` CHECK (mirror line 60-64). **Migrácia 069** (`server_default='standalone'` + data step `nex-inbox`→`nex_module`, review #12). + `deploys` + `deploy_members` tabuľky.
- `backend/schemas/project.py` — `ProjectArchetype = Literal["nex_module","standalone"]`; `archetype` na `ProjectCreate` (default `standalone`) + `ProjectRead`; **omit z `ProjectUpdate`** (nemenné).
- `frontend/src/pages/NewProjectPage.tsx` — two-way archetype selector pod category toggle (`:226-259`); Deploy picker pre `nex_module`; pass `archetype` v payload (`:171`).
- `backend/services/template_bootstrap.py:181-182` — pridaj `--archetype project.archetype` (kolmé na `--variant general`).
- `init.sh` — accept/validate `--archetype`; vyber FE skeleton variant (login vs token-launch); vetvi ktoré charter rules platia.
- `frontend-skeleton/` — templated auth surface (login page set vs LaunchRequired set; `authStore` mode); archetype-A skeleton **netemplatuje žiadnu user tabuľku** (review #7).
- `designer/CLAUDE.md.tmpl:276` — prose judgment → hard archetype-driven auth rule (+ "exactly two methods, never email").
- `auditor/CLAUDE.md.tmpl` — archetype-conditional compliance:
  - FAIL ak `nex_module` má `/auth/login` / `users`/`user_sessions`/auth-tabuľku v ANY DB / seed / external net ≠ `nex-proxy-net`.
  - FAIL ak `standalone` nemá `/auth/login`+register+seed+`must_change_password`.
  - FAIL na email-as-login alebo tretiu metódu.
  - FAIL ak `must_change_password` enforcement je len FE (chýba `require_password_changed` BE dependency, review #6).
- `backend/services/uat_provisioner.py` — archetype branch (§5.2–5.4); external-net guard rozšírený na OBA archetypy (`:637-640`, review #1).
- `backend/services/orchestrator.py` — `_release_auto_uat_deploy` branch (`:2732`); `_run_uat_deploy` post-up serve-verify (`:2268`); `_run_app_starts_smoke` archetype-conditional FAIL + FE-service assertion (`:2545`, review #13). `create_project_postscaffold.py:111` IPv4 + derived port.

### 6.2 Fázovaný roadmap (poradie podľa závislostí)

Nemôžeš scaffoldnúť modul pred tým, na čo ukazuje; nemôžeš dôverovať deployu, ktorý nevieš overiť. Preto verifikácia a Manager idú skoro.

| Fáza | Deliverable | Prečo toto poradie |
|---|---|---|
| **P0** | **gate_g deployability hardening** (4 bug fixy §5.6: archetype-conditional FE-serves probe, SKIP→FAIL, 127.0.0.1, post-up serve-check) + external-net guard (§5.3) | Nezávislé od archetypu. Opravuje bugy, čo pustili nex-asistent broken. MUSÍ pristáť PRVÉ, aby každá ďalšia fáza bola overená bránou, čo naozaj chytá broken buildy. |
| **P1** | **Postav NEX Manager** ako `standalone` ručným driveom dnešného flow | Je register + issuer, proti ktorému všetko ostatné rieši. Reuse Studio auth wholesale + gapy: `must_change_password` (model+BE gate) + token-mint + module registry (`identity_key_hash`) + `/identity` + central revocation. |
| **P2** | **`archetype` data model + schema + FE switch** (migr 069 vrátane nex-inbox backfill, `deploys`/`deploy_members`, NewProjectPage toggle) | Branch point. Pred scaffoldingom, aby create-project vedel čítať pole. |
| **P3** | **Per-archetype scaffolding** (token-launch FE skeleton variant + archetype charters + self-seed convention) | Potrebuje P2 (pole) + P1 (na čo modul ukazuje pri token-verify). |
| **P4** | **Provisioner two-mode + Deploy concept** (`provision_deploy`, merge-N-compose, single registry, per-Deploy Traefik, isolation fix, port allocation §5.7) | Najťažšia fáza. Potrebuje P1+P3+P0. **Vyžaduje Rozhodnutie 2 ako prerekvizitu** (review #8) — bez ≥2 modulov sa N-module collision logika neoverí. |
| **P5** | **Migrácia existujúcich appiek** (nex-inbox prvý → ledger/asistent per Rozhodnutie 2) | Posledné — potrebuje celý stroj funkčný. |
| (separátne) | metrics, orchestration-message-format | Mimo core architektúry; sekvenuj po P4 nezávisle. |

**Kritická cesta: P0 → P1 → P2 → P3 → P4 → P5.** P0 sa dá paralelizovať s P1/P2.

---

## 7. Migrácia existujúcich appiek (nex-inbox / ledger / asistent)

| App | Dnes | Cieľ | Cesta |
|---|---|---|---|
| **nex-inbox** | type-A (token-launch, **nula** user tabuliek) | `nex_module` v Deploy | **Najmenší lift — ale reálny auth-gate refactor, nie one-line swap** (§1.4, §4.6). Migr 069 explicitne nastaví `archetype=nex_module` (review #12). Module delta = 7 bodov §4.6: route `?lt=`, JWT verify, `SessionData` schema (drop `pid`, `tenant`→`deploy`+`module`), `/identity` + central revoke, cookie `nex_session`+`Secure` config, `NIB-*`→`NXM-*`, `.env` keys. **Prvý migrovaný** — dokáže Deploy+Manager round-trip e2e (1-module Deploy). |
| **nex-ledger** | type-B (vlastní `users`, `POST /auth/login`, seed admin) | Rozhodnutie 2 | Ak modul: DROP `/auth/login`+bcrypt+`users`+seed migr+`SEED_ADMIN_*`, ADD token-launch kontrakt + `/identity`. **Najťažší** — reálny auth rip-out + RBAC reconcile (review #10, viď Rozhodnutie 2). Ak standalone: dedí P0 fixy + external-net guard (`ledger-net` už private). |
| **nex-asistent** | type-B + **dva live bugy**: `nex-network: external:true` leak (`compose.yml:102-103`) + DB doslova `postgres` (`DB_HOST: postgres`, collision vektor) | Rozhodnutie 2 | Ak modul: rovnaký rip-out ako ledger PLUS custom Slovak-hunspell Postgres image ostáva ako module domain data (vlastný `<member>-db`, žiadne user tabuľky). **Bez ohľadu na archetype, oprav network leak + DB rename TERAZ** — live security bug. nex-asistent je canary čo dokazuje §5.4 izoláciu. |

---

## 8. Otvorené rozhodnutia pre Directora

Per krok-za-krokom protokol — predkladám po jednom, neimplementujem. Väčšina návrhu je single recommended design. Nižšie sú jediné genuine rozhodnutia, ktoré materiálne menia data model alebo vyžadujú tvoj produktový vstup.

### Rozhodnutie 1 — Počet hodnôt `archetype` (data model)

**Odporúčam dvojhodnotový `{nex_module, standalone}`, NEX Manager = `standalone` kotvený cez `deploys.manager_project_id`** (§2.1). Jedna os, jeden zdroj pravdy pre "kto je manager Deployu", dve scaffold vetvy namiesto troch.

Rovnocenná alternatíva (nie len horšia): **trojhodnotový `{nex_manager, nex_module, standalone}`** — Manager je vlastný archetype, lebo je issuer (login + vlastní users + token-mint), tvarovo iné než standalone. Výhoda: scaffold "Manager" priamo emituje token-mint vrstvu bez P1 ručného driveu. Nevýhoda: "som manager" je v dvoch miestach (archetype + deploys). Ovplyvní migráciu 069 CHECK + počet scaffold vetiev.

### Rozhodnutie 2 — Osud nex-ledger a nex-asistent (archetype voľba per app) — PREREKVIZITA P4

`nex-inbox` je jednoznačne `nex_module`. Ale `nex-ledger` a `nex-asistent` sú dnes type-B. **Otázka:** majú sa stať launched modulmi pod NEX Managerom (`nex_module`, ťažký auth rip-out), alebo ostať samostatné appky na zákazku (`standalone`, len bug fixy)? Business/produktové rozhodnutie.

**Toto NIE JE paralelná open item — je to prerekvizita P4 (review #8).** Dôvod: ak ledger aj asistent ostanú `standalone`, jediné čo exerсuje `provision_deploy` e2e je nex-inbox + Manager = **1-module Deploy**. N-module collision logika (`<member>-<svc>` naming, path-prefix routing, §5.4) by ostala **neoverená reálnymi appkami**. Buď konvertuj aj ledger, alebo postav throwaway 2. modul ako Deploy crash-test (mirror retired NEX Test princípu). Inak "dva moduly kolidujú na `backend`" je design claim, ktorý žiadna appka nedokáže.

**Pod-rozhodnutie 2a — RBAC reconcile pri konverzii ledger (review #10):** Manager rola je `admin|user`, ale ledger má dnes vlastnú 2-tier RBAC/compliance controls (verified-intentional diff). Pri konverzii ledger na modul treba rozhodnúť: (i) Manager rola je jediná a modul ju mapuje na lokálne capabilities (stateless, fine) — odporúčam; ALEBO (ii) modul drží per-(login) authz tabuľku keyed na Manager login (povolené — je to authz, nie register, žiadne heslá). Zvolený variant ide do "intentional diffs", aby ho Auditor neflagol. Relevantné len ak ledger ide cestou `nex_module`.

### Štandardný approval gate

Všetko ostatné je single recommended design s vyriešenými alternatívami v texte:
- Token signature **HS256** (§4.3) — RS256 re-vytvára distribučný problém a stráca centrálnu revokáciu v co-trusted izolovanom Deploy.
- Verify model **round-trip `/identity` per request** (§4.5) — nesie central revocation, nula extra cost (call už beží).
- `/identity` auth **per-module `IDENTITY_KEY`** (§4.1, §4.5) — nie zdieľaný launch kľúč (blast-radius fix).

Žiadne ďalšie otvorené design otázky — jediný potrebný vstup je tvoje schválenie pred štartom P0 (P0 nezávisí od ani jedného rozhodnutia, môže začať okamžite po approve).

---

## Load-bearing file:line index (overené proti kódu 2026-06-20)

- Archetype CHECK pattern na mirror: `backend/db/models/projects.py:60-64`; ďalšia migrácia = **069** (latest `068_drop_dialogue_tables.py`).
- Archetype seam: `backend/services/template_bootstrap.py:181-182`; FE toggle `frontend/src/pages/NewProjectPage.tsx:226`.
- Provisioner: `backend/services/uat_provisioner.py` — `provision_uat:708`, `derive_uat_slug:117`, external-net preserve `:637-640` (guard target, OBA archetypy).
- Orchestrator: `_run_uat_deploy:2268` (exit-0-only), `_run_app_starts_smoke:2535` SKIP `:2545-2549` (compose.is_file only — archetype-conditional fix), `_release_auto_uat_deploy:2732`.
- Scaffold smoke: `create_project_postscaffold.py:111` (hardcoded `localhost:8000`).
- Auth vzor (Manager reuse): NEX Studio `backend/db/models/foundation.py:19-60` (role CHECK `ri/ha/shu` `:39-42`, NO `must_change_password`), `backend/core/security.py`, `services/auth.py`, `migrations/versions/024_seed_admin_user.py`.
- Token-launch vzor (modul, REAL baseline): `/opt/projects/nex-inbox/backend/apps/auth_gate/router.py:33-85` (`?pid=&user=&token=` 3-param), `service.py:16-23` (static `LAUNCH_TOKEN` compare), `schemas.py:11-17` (`pid`+`tenant` mandatory), `config/settings.py:49-52` (`nex_inbox_session`, `Secure=False`).
- FE consumer: `/opt/projects/nex-shared/src/auth-store.ts:50` (`validateAfterLogin` hook, zero consumers), `ProtectedRoute.tsx`, `LaunchRequiredPage`.
- Live bug: `/opt/projects/nex-asistent/docker-compose.yml:102-103` (`nex-network: external:true`), `:13` (`DB_HOST: postgres` collision vektor).
- Charter auth line: `/home/icc/knowledge/templates/claude-project/.claude/agents/designer/CLAUDE.md.tmpl:276`.
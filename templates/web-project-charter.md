## Web projekt — administrácia a web (DEV-165)

Projekt má jeden backend a dva frontendy (tabuľka „Frontendy“ v `CLAUDE.md`). Čo pravidlá a zručnosti hovoria o
`frontend/`, platí pre **každý z nich** — preview (`<priečinok>/src/preview/`), `npm run type-check` a `lint` pred
commitom v jeho priečinku, skúšky, vernosť schválenému Vizuálu (Audítor porovná `git diff <Vizuál> -- frontend/ web/`).
Web má vlastné CI `.github/workflows/web.yml`. Pri každej obrazovke v Návrhu a v Pláne povedz, do ktorého patrí.

**Dve skupiny používateľov — oddelené účty, prihlásenia aj relácie:**
- Zamestnanci: `POST /api/v1/auth/login {username, password}`, `/auth/me`, `/auth/logout`. **Prvý správca sa
  zakladá menom**: v `.env.example` `# @customer required: „Meno prvého správcu“`, heslo `# @customer-secret
  required`, pri prvom prihlásení vynútená zmena hesla.
- Verejnosť: `POST /api/v1/web/auth/register {email, password}` (201; obsadený e-mail 409), `/web/auth/login`
  → `{access_token, token_type, user}`, `/web/auth/me`, `/web/auth/logout` (204) — kostra `web/` ich volá presne
  takto. Minimálnu dĺžku hesla určuje `web/src/config/settings.ts`, backend ju kontroluje sám; e-mail je jedinečný
  bez ohľadu na veľkosť písmen.
- Verejnosť má vlastnú tabuľku, nie rolu medzi zamestnancami. **Token webu neotvorí volanie administrácie a naopak** —
  bezpečnostný invariant v `safety_properties` s negatívnou skúškou v `release_smoke_test.sh`; ten overí aj
  prihlásenie do každého frontendu jeho spôsobom (kostra skriptu ukazuje ako, `SMOKE_FRONTENDS` ich menuje).
- Obnova hesla a potvrdenie e-mailu len keď ich žiada Zadanie (potrebujú poštový server projektu).

Aktualizácie sú len v administrácii. `docker-compose.yml` má službu `frontend` aj `web` (z `web/`, nginx so `/api` na
backend). V `.env.example` adresu webu označ `# @public-url`, adresu administrácie `# @public-url-admin`.

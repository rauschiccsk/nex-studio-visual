---
name: sablona-aplikacie
description: Projekt na šablóne aplikácie (nex-app-kit) — kam pridať obrazovku, položku menu, nastavenie, cestu servera, tabuľku, rolu agenta a odpovede náhľadu bez zmeny rámu. Použi pri každej obrazovke či funkcii takého projektu.
---

# Šablóna aplikácie — pridávam len svoje

Rám (ľavý panel, prihlásenie, Ako začať, Aktualizácie, Nastavenia, štandardné rozhranie servera, tabuľky `kit_*`)
je z balíka nex-app-kit. Aplikácia ho **používa**, nemení. Čo sa smie meniť, je len v krokoch nižšie; všetko ostatné
vo `frontend/src/App.tsx`, `frontend/src/main.tsx`, `frontend/src/preview/`, `backend/app/kit.py`, `backend/app/main.py`
a `docker-compose.yml` ostáva v tvare z kostry.

## Obrazovka a jej položka v ľavom paneli
1. Stránka `frontend/src/pages/AreasPage.tsx` z `nex-shared` (`<Page title subtitle actions>`, `DataTable`).
2. Cesta vnútri `<TemplateApp>` v `frontend/src/App.tsx`:
   `<TemplateApp manifest={manifest} …><Route path="/areas" element={<AreasPage />} /></TemplateApp>`
3. Položka v `frontend/app.manifest.json`: `"nav": [{ "label": "Oblasti", "path": "/areas", "icon": "🗂️" }]` —
   názov do 22 znakov, `adminOnly: true` pre obrazovku len pre správcu. Cesty `/`, `/getting-started`, `/updates`,
   `/settings` patria šablóne. Zlý manifest zastaví štart a každú chybu povie vetou — oprav všetky naraz.
4. Počet pri položke: `defineApp(data, { counts: { "/areas": useAreaCount } })` vo `frontend/src/app.ts`.
5. Stránka volá server klientom z `nex-shared`:
   `createApiClient({ baseUrl: "", apiPrefix: "/api/v1", getToken: () => null })` — relácia je cookie, ide sama.

## Vlastné nastavenie (Nastavenia → Systém)
1. `backend/app/kit.py`: `SETTINGS = [SettingDefinition(key="search_limit", default="20", value_type="int",
   label="Počet výsledkov", description="Koľko výsledkov ukáže hľadanie.", min=1, max=100)]`.
2. Kategória v manifeste: `"settings": { "categories": [{ "id": "search", "label": "Hľadanie", "description": "…",
   "prefixes": ["search_"] }] }`.
3. Čítanie: `kit.settings.get_int("search_limit")` (aj `get_str`, `get_float`, `get_bool`) — hodnotu natvrdo nepíš.

## Cesta servera a tabuľka
1. Router v `backend/app/routes/areas.py`; prihlásený `user = Depends(kit.current_user)`, len správca
   `Depends(kit.require_admin)`. Zapojenie v `backend/app/main.py`: `app.include_router(areas.router, prefix="/api/v1")`.
2. Model v `backend/app/models.py` na `app.db.Base`; človeka odkazuješ `ForeignKey("kit_users.id")`.
3. Migrácia: nový súbor v `backend/alembic/versions/` (`revision`, `down_revision` = predchádzajúca migrácia
   aplikácie, prvá má `None`). Tabuľky šablóny sa migrujú samy pred tvojimi a majú vlastný záznam verzie
   (`kit_alembic_version`) — `alembic/env.py` nemeň.
4. Skúšky v `backend/tests/` na Postgrese — `conftest.py` postaví schému cez `alembic upgrade head` do
   `TEST_DATABASE_URL`; ako ich spustiť, hovorí zručnosť `backend-tests`.

## Agent aplikácie (len aplikácia s AI)
Roly v manifeste: `"ai": { "roles": [{ "id": "assistant", "label": "Asistent" }] }` — každá dostane model a úsilie
v Nastavenia → Agenti. Server ich číta `kit.agent_choice("assistant")`; model natvrdo nepíš.

## Náhľad Vizuálu
`frontend/src/preview/handlers.ts`: `export const handlers = [...previewHandlers({ manifest }), ...vlastné]` — rám
(prihlásenie, Nastavenia, Aktualizácie, Ako začať) odpovedá sám. Doplníš len vlastné cesty, napr.
`http.get("*/api/v1/areas", () => HttpResponse.json([{ id: 1, name: "Stavebné povolenia" }]))`.

## Čo nerobím
Vlastný `Sidebar`/`AppShell`, prihlásenie, stránku Aktualizácií či Nastavení, cesty `/auth/*`, `/launch`, `/session`,
`/users`, `/sessions`, tabuľku používateľov, kópiu súborov nex-app-kit ani úpravu v `node_modules`. Chýba niečo v ráme?
Napíš to Manažérovi (`kind=question`) — nová schopnosť rámu je nová verzia nex-app-kit, nie úprava v projekte.

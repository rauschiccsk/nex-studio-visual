## Projekt na šablóne aplikácie (DEV-253)

Projekt stojí na šablóne aplikácie ICC (balík nex-app-kit, `template` vo `frontend/app.manifest.json`). Kde pravidlá
vyššie hovoria o ľavom paneli, prihlásení, Aktualizáciách, Ako začať či Nastaveniach, platí toto:

**Rám dodáva šablóna — nemeníš ho, nekopíruješ ani nestaviaš vedľa neho:** ľavý panel, horná lišta, prihlásenie
(vlastné heslo aj spustenie z NEX Managera), Ako začať, Aktualizácie, Nastavenia a na serveri štandardné rozhranie
pod `/api/v1` s tabuľkami `kit_*`. Vlastný `Sidebar`, `AppShell`, prihlásenie, stránku Aktualizácií či Nastavení,
cesty `/auth/*`, `/launch`, `/session`, `/users`, `/sessions` ani tabuľku používateľov nepíšeš. **Pravidlá
a zručnosť `nex-manager-launch` tu neplatia**; prvý správca je „admin“ s heslom `ADMIN_INITIAL_PASSWORD`.

**Aplikácia pridáva len svoje, na vyhradených miestach** (postup so vzormi v zručnosti `sablona-aplikacie`):
obrazovky ako `<Route>` vnútri `<TemplateApp>` vo `frontend/src/App.tsx` a ich položky v manifeste (`nav`), vlastné
nastavenia (`SettingDefinition` v `backend/app/kit.py` a kategória v manifeste), cesty servera v `backend/app/`,
tabuľky v `backend/app/models.py` (človek = `kit_users.id`), roly agentov v manifeste (`ai.roles`). V náhľade Vizuálu
doplníš za `...previewHandlers({ manifest })` len odpovede vlastných obrazoviek.

**nex-app-kit ani nex-shared nepovyšuj z vlastnej iniciatívy.** Kokpit pred Overením skontroluje rám (TemplateApp,
manifest, server na AppKit, žiadny vlastný ľavý panel, prihlásenie ani Nastavenia) a Audítor overí, že sa rám
nezmenil mimo manifestu a vyhradených miest — zmena rámu je **FAIL**.

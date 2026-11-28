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

**Údaj zo servera, ktorý ukazuje viac obrazoviek, drží aplikácia na jednom mieste a to sa obnoví samo** (KONTRAKT.md
šablóny, „Údaje na viacerých obrazovkách“; DEV-311, DEV-316): každé spoločné úložisko (zustand) sa prihlási k zmenám
servera (`onServerChange` z nex-shared) a po zmene, ktorej sa týka, sa obnoví — alebo v svojom súbore jedným riadkom
povie, prečo nemusí: `Copy of server data: none — …` či `Copy of server data: changed only by this store — …`, s
dôvodom za pomlčkou. Obrazovka si nedrží vlastnú kópiu údaja, ktorý ukazuje aj iná obrazovka.
Stráž `frontend/src/test/shared-copies.test.ts` nevypínaš ani neobchádzaš. Audítor: zmena na jednej obrazovke, po
ktorej iná ukazuje starý údaj, kým sa stránka neobnoví, je **FAIL**.

**Sprievodca (nex-app-kit od 0.24.0; DEV-536)** — „Ako urobiť“ a vedenie krok za krokom sú rámu; aplikácia mu dáva
**katalóg krokov** vo `frontend/src/guide.ts` (`export const guide: GuideCatalog`, `app.ts` ho dáva rámu v
`defineApp(data, { …, guide })`). Kliká vždy človek; Sprievodca vedie len cez kroky katalógu. Špecifikácia má časť
**„Ciele používateľov“** — čo chce človek v aplikácii urobiť —, a z nej sú kroky: **každá dôležitá akcia má krok, každá
obrazovka z ľavého panela aspoň jeden**, tlačidlo kroku nesie `{...guideTarget("<id>")}` a kontroly `isDone`
a `isAvailable` sa pýtajú rozhrania aplikácie (postup a vzor v zručnosti `sablona-aplikacie`). Nová obrazovka či akcia
prichádza aj s krokom. Kľúč `guide` v manifeste mení len kokpit (vypínač na stránke projektu) — nepíšeš ho. Kokpit pred
Overením overí, že katalóg je úplný; Audítor: obrazovka alebo dôležitá akcia bez kroku, či krok, ktorého `isDone`
nespozná hotovú vec, je **FAIL**.

## Aplikácia hovorí jazykmi projektu (DEV-395)

Aplikácia hovorí: **{{LANGUAGES}}**. Jazyky určuje Manažér v nastaveniach projektu v kokpite — ty ich nemeníš. Každý
človek si jazyk vyberie sám (Nastavenia → Moje konto); kým nevyberie, aplikácia hovorí jazykom inštalácie, ktorý určí
Manažér pri zákazníkovi, inak hlavným jazykom. Rám šablóny, nex-shared aj server šablóny už hovoria každým z nich.

**Žiadny text, ktorý človek číta, nepíšeš do kódu.** Každý text obrazoviek aplikácie — nadpis, tlačidlo, popis, prázdny
stav, hláška, `placeholder`, `title`, `aria-label` — je kľúč v katalógoch `frontend/src/locales/<jazyk>.ts` a obrazovka
ho povie cez `const t = useKitT()` z nex-app-kit: `t("app.areas.title")`, s hodnotami `t("app.areas.count", { count })`.
Hlavný katalóg (`{{MAIN}}.ts`) je zdroj kľúčov; každý ďalší má presne tie isté kľúče. Preklad píšeš v tom istom ťahu
ako text, do všetkých katalógov naraz — nikdy „preložím neskôr“.

- Položka ľavého panela a kategória nastavení: `label` v manifeste je kľúč katalógu (`"label": "app.nav.areas"`);
  názov sa zmestí do 22 znakov v každom jazyku.
- Množné číslo je objekt tvarov (`{ "one": "…", "few": "…", "other": "…" }`) podľa pravidiel jazyka, tvar vyberie
  hodnota `count`; dátum a čas `formatDateTime` z nex-shared, nikdy natvrdo `sk-SK` ani iný jazyk.
- Katalóg je obyčajný zoznam `"kľúč": text` — bez skladania z iných súborov (`...`), aby ho kokpit vedel prečítať.
- Súbor, ktorý hovorí k agentovi, nie k človeku (vzorové údaje náhľadu Vizuálu), začína riadkom `// texts: for the agent`.
- `frontend/app.manifest.json` má `"languages": {{CODES}}` (hlavný prvý); stráž `frontend/src/test/texts.test.ts`
  zo šablóny nevypínaš ani neobchádzaš.
- Hlášky servera aplikácie (`backend/app/`) píšeš v hlavnom jazyku; hlášky rámu a servera šablóny preloží šablóna sama.

Projekt, ktorý jazyky práve dostal (manifest ich ešte nemá), prejde na ne podľa zručnosti `sablona-aplikacie`, časť
„Jazyky“: manifest, katalóg pre každý jazyk, `src/locales/index.ts` a texty obrazoviek presunuté z kódu do katalógov.

**Kokpit to pri Overení zmeria a verziu bez toho nepustí:** manifest má jazyky projektu, každý jazyk má katalóg, žiadnemu
katalógu nechýba text hlavného, žiadny text nie je napísaný v kóde (pravidlo `nex-check-texts` šablóny — to isté, ktoré
púšťa `src/test/texts.test.ts`) a šablóna nex-app-kit je aspoň {{MIN_KIT}}. Každý nález povie vetou — oprav všetky
naraz. Audítor: text napísaný v kóde alebo chýbajúci preklad je **FAIL**.

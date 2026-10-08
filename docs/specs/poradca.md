# Poradca — návrh pre kokpit nex-studio-visual

> Andros Dedo, 05.10.2026. Stav: **SCHVÁLENÉ Directorom 05.10.2026**, tiket ICCINT-167 (Robí: Andros Dedo).
> Director pri schválení doplnil model pre každého agenta bez natvrdo zapísaných verzií — časť 4.6.
> Návrh prešiel nezávislou kontrolou proti kódu (05.10.); jej nálezy sú zapracované (B1–B9 v texte).

## 1. Zámer

Director 05.10.2026: ľudia, ktorí v kokpite projektujú (Zoltán, Tibor, Nazar), majú mať **paralelne**
agenta, s ktorým počas vývoja **prekonzultujú, čomu nerozumejú**, a ktorého pošlú **pozrieť veci
„zozadu", kam z obrazovky nevidia**. Názov: **Poradca**. Andros Dedo ostáva, kde je; Poradca je súčasť
kokpitu a pracuje bez neho. Schválený smer: samostatný poradca v kokpite, **len číta**, dá sa použiť
**aj počas stavby**, nevzniká presunom Deda.

## 2. Čo kokpit má dnes (zmerané v4.40.32)

| čo | stav | prečo to nestačí |
|---|---|---|
| **Konzultácia** (`orchestrator._begin_consult` ~r. 3604, `run_consult_turn` ~r. 7728, `consult_sandbox.py`) | otázka na **hotovej** verzii, agent len číta (Read, Grep, Glob) | len pri `current_stage == 'done'`; beží cez stav verzie, popri stavbe nejde; vidí len kód; **na PROD nebola použitá ani raz** (0 z 3 717 správ); kontajner sa podľa kódu ani nespustí (`--user andros`, obraz taký účet nemá — `build_sandbox.py:199`; naživo neoverené); pripája celý `~/.claude` na zápis; nastavenia projektov (`--setting-sources user,project`) povoľujú `WebFetch`/`WebSearch` a zoznam zakázaných nástrojov ich neobsahuje |
| **Izolovaná stavba** (`build_sandbox.py`) | dočasný domov, prihlásenie premennou `CLAUDE_CODE_OAUTH_TOKEN`, `--cap-drop=ALL`, oplotená sieť, živý priebeh `stream-json` | vzor, na ktorom Poradca postaví svoj kontajner |
| **Terminál s agentom** (`agent_terminal.py`) | kód je, `DebugTerminalDrawer` nie je vložený nikde | nie je cesta pre ľudí v kokpite |
| **Prihlásenie agentov** | jedno predplatné Claude MAX (`CLAUDE_CONFIG_DIR`, compose PROD r. 78) | Poradca čerpá z toho istého týždenného limitu ako stavby |
| **UAT** | 8 inštalácií, všetky `postgres:16-alpine`; 6 vytvoril kokpit, 2 ručne písané; živým projektom patrí 5 | do databázy UAT dnes z kokpitu nevidí nikto |

## 3. Ako bude Poradca fungovať — pohľad človeka

### Kde ho nájdem
Položka **Poradca** v bočnej ponuke pod **Riadiacim centrom**. Bez pripnutého projektu alebo bez prístupu
k nemu je zašednutá s nápovedou (rovnaké vety ako Riadiace centrum). Prístup má vlastník projektu a účet
admin — ako všade v kokpite. „Autor rozhovoru" je prihlásený účet.

### Rozhovor
- Vľavo **zoznam mojich rozhovorov** k projektu (názov podľa prvej otázky, dátum), tlačidlo **Nový rozhovor**;
  vpravo rozhovor a pole na otázku.
- Nad poľom **o čom sa rozprávame**: projekt a verzia (predvolene vybraná verzia) s fázou a stavom;
  dá sa zmeniť, alebo zvoliť „celý projekt".
- Rozhovor pokračuje, kde som skončil, aj na druhý deň.
- **Rozpísaná otázka sa nestratí** (v4.43.4, DEV-24, Director 08.10.2026: „ak začnem písať do promptu pre
  Poradcu a potom prekliknem na Centrum riadenia … keď sa vrátim môj napísaný prompt už tam nie je"). Pole si
  otázku pamätá pre každý rozhovor zvlášť a pre nový rozhovor projektu, kým ju neodošlem (`useDraft`, ako pole
  rozhovoru v Riadiacom centre). Návrat na Poradcu — aj cez bočnú ponuku, ktorá otvára holé `/poradca` —
  otvorí rozhovor, v ktorom som bol (pamätá si ho prehliadač pre každý projekt; zmazaný rozhovor sa neotvorí).
  Obnovená otázka je označená vetou „Obnovený rozpísaný text — pokračuj, alebo ho prepíš."; zmaže sa až po
  úspešnom odoslaní.
- **Kým pracuje, vidím čo robí**, riadok po riadku: „Čítam `backend/services/x.py`", „Pozerám 200 riadkov
  logu backendu v UAT", „Pýtam sa databázy UAT", „Rozoberám, čo agent stavby robil za poslednú hodinu".
  Tlačidlo **Zastaviť** otázku preruší.
- **Rozhovor ide za textom** (v4.43.5, DEV-25, Director 08.10.2026: „Ak chcem čítať čo píše musím ja skrolovať
  obrazovku.“): nový riadok priebehu aj hotová odpoveď posunú rozhovor na koniec, otvorený rozhovor začína na
  konci. Kto si odroloval vyššie a číta staršiu časť, toho nový riadok nevytrhne; keď sa vráti na koniec, ide
  rozhovor znovu za textom. Vlastná otázka ho na koniec vráti vždy.
- Pod odpoveďou: **ako dlho trvala a koľko stála**.
- **Premenovať a vymazať** (v4.42.0, Director 05.10.2026: „chýba mi premenovanie rozhovoru a vymazanie
  rozhovoru"). Pri rozhovore v zozname ceruzka a kôš. Premenovanie priamo v zozname: Enter uloží, Esc zruší,
  odchod z poľa uloží; názov je jeden riadok do 200 znakov. Poradie v zozname určuje posledná otázka, nie
  úprava názvu ani voľba „o čom sa rozprávame". Vymazanie sa potvrdí v riadku („Vymazať … natrvalo? Text sa
  nedá obnoviť; cena ostane v Nákladoch."). Vymažú sa otázky, odpovede, kroky, chyby, názov a celý záznam
  sedenia Claude Code na disku; ostane len spotreba a čas odpovedí, aby Náklady sedeli. Kým Poradca
  odpovedá, kôš je zašednutý s dôvodom „najprv odpoveď zastav". Smie autor rozhovoru a účet admin.

### Čo Poradca vidí („zozadu")
1. **Kód a dokumenty projektu** a históriu zmien (kto, kedy, čo).
2. **Stavbu** — fázu, stav, dôvod zastavenia, rozhovor Manažéra s agentom, plán úloh; a **tlačidlá, ktoré
   Manažér na obrazovke práve vidí** (z tých istých dát, z ktorých kreslí obrazovka).
   - **Karty rozhodnutí počas konzultácie** (v4.43.6, DEV-28, Director 08.10.2026: „Poradca nevidí do karty …
     popisuje len všeobecne“): nástroj `stavba` pridá karty tak, ako ich vidí Manažér — kolo, ktorá karta je na
     rade, pri každej otázka, vysvetlenie a možnosti doslova (s označením odporúčanej), pri rozhodnutých zvolenú
     možnosť a pokyn pre AI partnera. Číta ich tými istými funkciami, z ktorých kokpit karty skladá
     (`_latest_consultation`, `_consultation_answers`). Charta Poradcu mu káže radiť kartu a možnosť doslova
     a pokyn pre agenta písať ku karte, ktorá je na rade (vloží sa do jej poľa „Pokyn pre AI partnera“, DEV-26).
3. **Čo robil agent stavby** — rozobrané na kroky: ktoré súbory čítal, aké príkazy spustil, kde zlyhal.
4. **Kontajnery a logy** tohto projektu: živý náhľad z Vizuálu, dočasná databáza stavby, inštalácie UAT
   (tie isté, ktoré človek vidí na obrazovke UAT).
5. **Databázu UAT len na čítanie** — dotazom, ktorý v odpovedi ukáže; stĺpce s heslami a kľúčmi mu
   databáza vôbec nevydá.
6. **Zostavenie (CI)** — či prešlo, a keď nie, koniec logu zlyhaného kroku.
7. **Znalostnú bázu ICC** s právami človeka, ktorý sa pýta.

### Čo nevidí a nesmie
- **Nič nezmení**: nemá nástroj na zápis ani spúšťanie príkazov, projekt je pripojený len na čítanie
  (garantuje jadro systému), databáza UAT ho pustí len čítať.
- **Nevidí PROD** (ostré inštalácie zákazníkov), **iné projekty**, trezor prístupov, súbory `.env`, kľúče,
  nastavenia kontajnerov a **nedostane sa na internet** (okrem samotného Claude).
- **Tajomstvá neukáže** — čo vyzerá ako heslo či kľúč, kokpit nahradí „‹skryté›" skôr, než to Poradca dostane,
  a ešte raz pred zobrazením odpovede.

### Keď treba niečo zmeniť
Poradca poradí, **ktorým tlačidlom** to urobiť, a podľa situácie ponúkne:
- **„Vložiť do Riadiaceho centra"** (počas stavby) — pokyn sa vloží do poľa, ktoré práve prijíma text: keď
  agent čaká na odpoveď (otázka, chyba, kontrola), do poľa v lište nad rozhovorom („Tvoja odpoveď…“); počas
  konzultácie do aktuálnej karty rozhodnutia, do poľa „Pokyn pre AI partnera“ (viacriadkové, DEV-26); inak do
  poľa rozhovoru. Ktoré pole to je, rozhoduje jediné pravidlo `inputOwner` (úplný zoznam všetkých dôvodov
  zastavenia v `frontend/src/components/riadiace/blockRecovery.ts`; nový dôvod neprejde kontrolou typov, kým
  ho niekto nezaradí) a Riadiace centrum ho použije až podľa známeho stavu stavby. Pokyn, ktorý ostal v poli
  rozhovoru, keď text prevzalo iné pole, sa presunie tam. Vždy s označením „Pokyn od Poradcu“ a odošle ho
  človek sám (DEV-22). Keď žiadne pole text neprijme (stavba čaká na opravu kokpitu, hotová verzia), tlačidlo
  je zašednuté s dôvodom; server a obrazovka sa v tom zhodujú
  (`tests/test_poradca_instruction_target_matches_cockpit.py`).
- **„Uložiť do Zásobníka"** (keď zmena do bežiacej stavby nepatrí alebo je verzia hotová) — požiadavka sa zapíše
  do Zásobníka projektu ako REQ-N a nič viac; pod odpoveďou potom stojí „Uložené v Zásobníku ako REQ-N.“
  s tlačidlom „Otvoriť Zásobník“, druhé kliknutie nezaloží druhú požiadavku. Verzia z nej nevzniká — do ktorej
  verzie požiadavka pôjde, rozhoduje Director (v4.43.7, DEV-29: „O verziách rozhodujem ja. Treba, aby zapísal len
  do zásobníku.“). Do v4.43.6 sa tlačidlo volalo „Založiť novú verziu z tejto požiadavky“ a zakladalo aj ďalšiu
  verziu so zadaním z požiadavky; z odpovedí Poradcu tak nevznikla ani jedna. Blok v odpovedi je
  `<poziadavka-do-zasobnika>`; staršie odpovede s `<poziadavka-na-novu-verziu>` sa ukladajú rovnako.

### Počas stavby
Beží **vedľa** stavby: neprepína jej stav, nečaká na ňu, nezastaví ju. Agent stavby o rozhovore nevie.
Viac ľudí sa môže pýtať naraz.

### Kto vidí rozhovory
Autor; Director (účet admin) vidí všetky.

### Náklady a limit
Autora rozhovoru kokpit nezmaže (od v4.43.2, ICCINT-169) — s rozhovormi by zmizla aj cena odpovedí; ponúkne deaktiváciu. Cena pri každej odpovedi (od v4.43.0 cenníkom Anthropic z Claude Code a kurzom ECB, aj zastavená odpoveď — `naklady-cennik.md`); v **Nákladoch** riadok **Poradca** zvlášť od fáz stavby. Naraz najviac **3 otázky**
v celom kokpite (nastaviteľné), ďalšia počká s vetou prečo. Strop jednej otázky 15 minút. Model a úsilie
v **Nastaveniach** ako pri AI Agentovi; predvolený Opus (vždy najnovší, 4.6), úsilie `high`.

## 4. Ako to funguje vnútri

### 4.1 Kontajner Poradcu
Každá otázka = dočasný kontajner z obrazu backendu (`docker run --rm`), podľa vzoru izolovanej stavby:
- `--user 1000:1000`, `--cap-drop=ALL`, `--security-opt no-new-privileges`, **oplotená sieť**
  (`build_db.create_fenced_network`, ICCINT-21) — hostiteľ nedosiahnuteľný;
- dočasný domov (tmpfs), prihlásenie len `CLAUDE_CODE_OAUTH_TOKEN`;
- **claude v obmedzenom režime** (B1, B2): `--restricted --tools Read,Grep,Glob --strict-mcp-config`
  `--mcp-config <nástroje Poradcu>` + `--permission-mode dontAsk` s povolenými nástrojmi Poradcu.
  Podľa `claude --help` (2.1.289) obmedzený režim zruší nástroje spúšťajúce kód aj `WebFetch`, ignoruje
  nastavenia používateľa a projektu (tam je dnes `WebFetch`/`WebSearch` povolený) a **obmedzí čítanie
  súborov na pracovné priečinky** — teda nie `/proc/self/environ` s tokenom;
- pripojené len na čítanie: projekt (`/opt/projects/<slug>`, pracovný priečinok); pred každou otázkou
  sa strom prejde a **každý** súbor `.env*`, `*.pem`, `*.key`, `id_*`, `*.p12` sa prekryje prázdnym (B8);
- na zápis len priečinok tohto rozhovoru, pripojený presne tam, kam claude ukladá záznam podľa pracovného
  priečinka (`…/projects/-opt-projects-<slug>`), aby `--resume` fungoval; **záznamy agenta stavby sa
  nepripájajú** (B3);
- výstup `stream-json` (ako stavba) → živý priebeh; do databázy a WebSocketu ide **len nástroj a cieľ**,
  nie obsah (B7);
- **zlyhá nahlas**: keď sa kontajner nedá spustiť, otázka skončí chybou s dôvodom; nikdy neustúpi na beh
  mimo kontajnera.
- **meno súboru nesmie meniť pripojenie** (nález nezávislej bezpečnostnej previerky 05.10.2026, opravené
  pred nasadením): súbor na prekrytie, ktorého cesta obsahuje čiarku, úvodzovky či riadiaci znak, by do
  zápisu `--mount` vpašoval vlastný `source=` a docker by pod projekt pripojil ľubovoľnú cestu hostiteľa.
  Taký súbor Poradca odmietne s jeho menom (premenovať) — a druhá poistka to isté overí pri skladaní celého
  `docker run`. Previerka ďalej opravila: kroky vstavaných nástrojov a chybový výstup Claude teraz idú
  filtrom tajomstiev; trezor prístupov sa číta cez jeho službu, nie priamo zo súborov.

### 4.2 Nástroje na pohľad „zozadu"
Všetko „zozadu" podá **backend**. Kontajner má v sebe malého prostredníka (MCP cez stdio), ktorý sa spojí
s backendom cez **unixový socket platný len pre jednu otázku**; sieť ostáva oplotená. Socket leží
v priečinku hostiteľa, ktorý vidí backend aj kontajner — **pribudne jedno pripojenie v PROD compose kokpitu**
(súbor je mimo repozitára, mení sa pri nasadení s potvrdením Directora). Meno kontajnera, adresár ani
databázu neurčuje AI, ale backend z projektu. Každý výstup prejde filtrom tajomstiev (4.4).

| nástroj | čo vráti | strop |
|---|---|---|
| `stavba` | fáza, stav, dôvod, `next_action`, rozhovor; tlačidlá z toho istého balíka, z ktorého kreslí obrazovka | 50 správ |
| `plan_uloh` | plán úloh verzie a stav úloh | — |
| `git_historia`, `git_zmena` | `git log` / `git show` jedného commitu; commit len podľa vzoru `^[0-9a-f]{7,40}$`, `--end-of-options`, cesty za `--`, bez shellu (B6) | 100 commitov / 2 000 riadkov |
| `zaznam_agenta` | záznam agenta stavby rozobraný na kroky (nástroj, súbor/príkaz, chyba) (B3) | posledných 200 krokov |
| `kontajnery`, `logy` | stav a koniec logu kontajnerov projektu (Vizuál, databáza stavby, UAT jeho zákazníkov — väzba cez tabuľku zákazníkov, B9); bez `docker inspect` | 500 riadkov |
| `ci` | stav (`ci_status.snapshot`) + **nové**: koniec logu zlyhaného kroku | 300 riadkov |
| `znalostna_baza` | vyhľadávanie v KB s právami používateľa (`kb_access`) | 10 výsledkov |
| `databaza_uat` | jeden dotaz `SELECT`/`WITH`/`EXPLAIN` | 200 riadkov, 10 s |

### 4.3 Databáza UAT len na čítanie (B4, B5)
- V databáze UAT účet **`poradca_ro`** bez práv správcu, `default_transaction_read_only = on`,
  `statement_timeout = 10s`. Právo **SELECT len na stĺpce bez tajných mien** (`password`, `hash`,
  `token`, `secret`, `key`, `session`…) — zoznam sa vygeneruje zo schémy pri každom nasadení. Stĺpce
  s heslami tak nevydá ani `row_to_json`, alias či `substr` — stráži to databáza, nie filter.
  Tvarový filter hodnôt ostáva ako druhá vrstva.
- **Celé tabuľky s prístupmi** (meno obsahuje `credential`, `secret`, `password`, `token`, `session`)
  Poradca nedostane vôbec, ani ich „nevinné" stĺpce; zašifrovaný obsah (`cipher`, `nonce`) tiež nie.
  Zmerané 05.10.2026 v živom UAT NEX Inboxu: `email_credentials` mala skrytý len `key`, šifrovaný text by
  bol čitateľný — opravené pred prvým použitím.
- Účet zakladá správca databázy z `POSTGRES_USER` inštalácie (v živých UAT nexmanager, nexweb, nex_inbox —
  rola `postgres` tam neexistuje; zmerané pred prvým zápisom).
- Spustenie: `docker exec -u postgres <db> psql -X -At -U poradca_ro -c <dotaz>` ako pole argumentov;
  dotaz s `\` sa odmietne; jediný príkaz overí rozbor. Pri nasadení sa overí, že databáza nemá
  rozšírenia `dblink` ani `postgres_fdw`.
- Účet zakladá **krok po spustení UAT** v `_run_uat_deploy` (provisioner databázu nespúšťa), opakovateľný
  pri každom nasadení — zachytí aj zmeny schémy. Do ručne písaného compose sa nesiaha. Pre 5 živých
  inštalácií UAT ho raz spustím **so súhlasom Directora**, inštaláciu po inštalácii; vratné (`DROP ROLE`).
- Predpoklad na overenie pri stavbe: prihlásenie na lokálnom sockete v `postgres:16-alpine` bez hesla.

### 4.4 Ochrana tajomstiev
„Bez internetu" stojí na nástrojoch, nie na sieti: oplotená sieť (rovnaká ako pri stavbe) nechá von
spojenie na Claude, ktoré Poradca potrebuje; webové nástroje a príkazy mu odoberá obmedzený režim a žiadny
nástroj kokpitu nesťahuje nič podľa pokynu agenta. Nový nástroj, ktorý by niečo sťahoval, by to otvoril.

Tri vrstvy: (1) **čo Poradca nedostane vôbec** — obmedzený režim, prekryté súbory, stĺpce bez práva,
žiadne priame záznamy agenta; (2) **známe hodnoty** v backende — prístupy projektu z trezoru, tajné premenné
z `.env` UAT, `CLAUDE_CODE_OAUTH_TOKEN`, kľúč Deda, kľúč podpisu prihlásení — nahradené presnou zhodou;
(3) **tvary** — `token=`, `Bearer`, `password=`, `SECRET_KEY=`, heslo v adrese databázy, JWT. Vrstvy 2 a 3
bežia na výstupe každého nástroja aj na odpovedi. Skúšky len na umelých hodnotách.

### 4.5 Dáta, API, obrazovka
- Migrácia `102` (`101` je model podľa rodiny, 4.6): `poradca_conversations`, `poradca_messages` (poradie, autor, text, kroky = nástroj + cieľ,
  spotreba a cena, trvanie, stav `running|done|failed|stopped`); rola `poradca` v obmedzení
  `user_agent_settings` (dnes `('ai_agent','auditor')`, migrácia 069) aj v `schemas/user_agent_setting.py`;
  nastavenie súbežnosti.
- API `/api/v1/poradca/…` (rozhovory, otázka, zastavenie, WebSocket priebehu); `authz` ako inde.
- Migrácia `103` (v4.42.0): `poradca_conversations.deleted_at`. Vymazanie (`DELETE /conversations/{id}`)
  riadky nezmaže — `metrics._poradca_rows` sčíta spotrebu z `poradca_messages` a tvrdé zmazanie by z Nákladov
  zobralo minuté peniaze. Text, kroky, chyba a názov sa prepíšu na prázdne (názov „Vymazaný rozhovor").
  Priečinok `sessions/<id>` sa pod zámkom presunie do `trash/` jedným krokom (`os.rename`, ten istý disk);
  zlyhá presun → 500 a rozhovor ostane celý; zlyhá zápis do databázy → priečinok sa vráti. Súbory z koša sa
  mažú až po zápise, mimo zámku; čo nejde (kontajner z minulého behu ešte píše), dozmaže štart backendu
  (`sandbox.sweep_trash`). Vymazaný rozhovor je pre rozhranie 404 všade (zoznam, detail, otázka,
  premenovanie, voľba verzie, nová verzia, WebSocket); otvorené karty dostanú udalosť `deleted`. Otázka aj
  vymazanie zamknú riadok rozhovoru (`SELECT … FOR UPDATE`), takže sa neprekrížia; premenovanie a voľba
  verzie zapisujú len s podmienkou `deleted_at IS NULL`. Počas bežiacej odpovede je vymazanie 409.
  Premenovanie `PUT /conversations/{id}/title`; znak NUL v názve či otázke je 422.
- Kontajner otázky nesie značku `build_db.OWNER_LABEL` — keď prežije reštart backendu, odprace ho pri štarte
  `reap_orphans` spolu s jeho sieťou (nález previerky v4.42.0; predtým bežal ďalej a písal do záznamu).
- Vymazaný text ešte ostáva v zálohách databázy kokpitu, kým sa neobmenia (zmerané 05.10.2026:
  `/opt/infra/platform/scripts/backup-server.sh` robí denný `pg_dump` databázy kokpitu, restic drží 7 denných,
  4 týždenné a 6 mesačných), a v starých verziách riadkov, kým ich Postgres neupratá. Priečinok
  `/opt/data/nex-studio-visual/poradca` záloha servera nezahŕňa — záznam na disku po vymazaní nie je nikde.
- Charta `templates/poradca-charter.md`: len číta a radí; tvrdenie dokladá tým, čo prečítal; odporúča len
  tlačidlá z nástroja `stavba`; zmenu aplikácie smeruje do Zásobníka (od v4.43.7, DEV-29; predtým do novej verzie).
- „Založiť novú verziu" čítalo požiadavku zo správy stavby (`change_request.py`) — dostalo druhý zdroj:
  štruktúrovaný výstup Poradcu. Od v4.43.7 (DEV-29) je z toho „Uložiť do Zásobníka" a `change_request.py`
  je odstránený (nikto iný ho nepoužíval).
- Frontend: položka ponuky, `/poradca`, priebeh, zastavenie, cena, nastavenie modelu, Náklady, dve tlačidlá
  prepojenia; `npm run codegen`. `/health` ukáže pripravenosť Poradcu namiesto `consult_sandbox`.

### 4.6 Model pre každého agenta bez natvrdo zapísanej verzie (doplnok Directora)
Director 05.10.2026: *„aby v Nastaveniach bolo možné nastaviť pre každého agenta model Opus 5.5 … aby som
nemusel opravovať aplikáciu, ak sa zmení verzia modelu."*

**Dnes (zmerané v4.40.32):** `AgentModel` v `backend/schemas/user_agent_setting.py` je zoznam štyroch verzií
(`claude-opus-5`, `claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`); ten istý zoznam je
natvrdo v `SettingsPage.tsx` a `MetricsPage.tsx`; `DEFAULT_AGENT_MODEL = "claude-opus-5"`,
`DEFAULT_HELPER_MODEL = "claude-haiku-4-5-20251001"` (`orchestrator.py`); šablóny `templates/*-settings.json`
majú `claude-opus-4-8`. V `user_agent_settings` na PROD nie je ani jeden riadok — všetko beží na predvolenom
(záznam behov: 1 025 behov `claude-opus-5`, 239 `claude-opus-4-8`).

**Riešenie:** ukladá sa a posiela **rodina** — `opus`, `sonnet`, `haiku` (`claude --model` prijíma meno
rodiny a spustí jej najnovšiu verziu; zmerané 05.10.2026 na ANDROSe s CLI 2.1.289: `opus` →
`claude-opus-5-5`, `sonnet` → `claude-sonnet-5-5`, `haiku` → `claude-haiku-4-5-20251001`). CLI sa na
hostiteľovi aktualizuje sám (verzie 2.1.286/287 z 01.10., 2.1.289 z 05.10.) a kokpit ho má pripojený
z hostiteľa — nová verzia modelu príde bez zmeny aplikácie.
- `AgentModel = Literal["opus", "sonnet", "haiku"]`; predvolené `opus` (agenti, Poradca) a `haiku`
  (pomocníci AI Agenta). Nástroj na spúšťanie pomocníkov v Claude Code prijíma práve mená rodín.
- Migrácia: uložené úplné mená sa prevedú na rodinu podľa slova v mene (na PROD 0 riadkov; chráni iné databázy).
- Pri voľbe v Nastaveniach je vidno, **ktorá verzia naposledy naozaj bežala** — backend ju číta zo záznamu behov
  (`payload.usage.model`, ktorý plní CLI z `modelUsage`, a správy Poradcu), nie zo zoznamu v kóde. Meno na
  zobrazenie sa skladá z úplného mena (`claude-opus-5-5` → „Opus 5.5").
- Zoznam modelov v Nákladoch (ručne zadaný náklad) berie tie isté rodiny; ceny sú už dnes podľa rodiny.
- Šablóny agentov dostanú `opus`; existujúcim projektom sa súbor nemení — `--model` pri spustení má prednosť.
- Mimo rozsahu: rodina `fable` (CLI ju pozná, kokpit pre ňu nemá cenu) — pridá sa, keď ju Director bude chcieť.

## 5. Čo sa stane s dnešnou Konzultáciou
Poradca ju **nahradí**: na PROD ju nikto nepoužil, kontajner sa jej podľa kódu nespustí a nesie dve diery
(celý `~/.claude` na zápis, povolený `WebFetch`). Na hotovej verzii bude v Riadiacom centre namiesto poľa
na otázku tlačidlo **„Opýtaj sa Poradcu"**. Odstráni sa cesta Konzultácie (backend, obrazovka, skúšky;
v `templates/ai-agent-charter.md` zmienka o nej nebola — „konzultuj" tam patrí Príprave a kartám
rozhodnutí). Správu do Riadiaceho centra hotovej verzie backend odmietne vetou, ktorá menuje Poradcu. **Neruší sa** „kolo konzultácie" s kartami rozhodnutí počas stavby
(`DecisionCardsBar`) — iná vec s podobným menom.

## 6. Postup a veľkosť

Verzia kokpitu **4.41.0**.

| # | časť | môj čas |
|---|---|---|
| 1 | kontajner (obmedzený režim, oplotená sieť, prekrytia, socket a prostredník), rozhovory, priebeh, zastavenie, filter, migrácia | ~3,5 h |
| 2 | nástroje „zozadu" (vrátane rozboru záznamu agenta, konca logu CI, účtu `poradca_ro` po stĺpcoch v kroku nasadenia UAT) | ~4 h |
| 3 | obrazovka, ponuka, nastavenia, Náklady, „Vložiť do Riadiaceho centra", nová verzia z Poradcu | ~2,5 h |
| 4 | nahradenie Konzultácie | ~1,5 h |
| 5 | brány (skúšobňa, CI 8,6–10,6 min, viackrát), nezávislá bezpečnostná previerka a opravy, nasadenie so zmenou PROD compose, živé overenie | ~2,5 h |
| 6 | model podľa rodiny pre každého agenta (4.6) | ~1 h |

**Spolu: mne ~15 h (asi dva dni práce); človeku ~90 h (asi dva a pol týždňa).** Prvý odhad 10 h bol nízky —
kontrola ukázala, že kalibrácia počítala len prvé commity bez neskorších opráv, a pribudli body B1–B9.
Mimo: jednorazové založenie `poradca_ro` v 5 živých UAT so súhlasom (~20 min).

**Istota: stredná.** Číslo pohnú: prostredník MCP cez socket (v kokpite nový), prihlásenie `psql` bez hesla
v UAT, správanie obmedzeného režimu claude v kontajneri (overí dymová skúška).

## 7. Ako sa pozná, že to funguje
- Počas bežiacej stavby sa Tibor opýta „prečo agent stojí" a dostane odpoveď opretú o kroky agenta; stavba
  beží ďalej bez prerušenia.
- „Koľko faktúr je v UAT v stave chyba" vráti číslo aj dotaz; `UPDATE` databáza odmietne; `SELECT` na stĺpec
  s heslom odmietne.
- **Dymové skúšky naostro:** Poradca nedokáže prečítať `/proc/self/environ`, súbor `.env` projektu ani súbor
  mimo projektu; `WebFetch` a `WebSearch` sú odmietnuté; umelé tajomstvo v logu sa ukáže ako „‹skryté›"
  v odpovedi aj v uloženej správe.
- Kontajner Poradcu nevidí iný projekt, Docker ani hostiteľa (skúšky na zoznam pripojení ako pri stavbe).
- Na hotovej verzii vedie „Opýtaj sa Poradcu" do Poradcu; cesta Konzultácie neexistuje.
- V Nastaveniach má AI Agent, pomocníci, Audítor aj Poradca voľbu Opus/Sonnet/Haiku; beh na Opuse zapíše
  `claude-opus-5-5` a Nastavenia ho ukážu ako „naposledy bežal Opus 5.5"; `command grep -rnE
  "claude-(opus|sonnet|haiku)-[0-9]" backend frontend/src templates` mimo skúšok nenájde nič.

## 8. Nález popri kontrole (mimo Poradcu)
PROD compose kokpitu `/opt/customers/dev/nex-studio-visual/docker-compose.yml` má **heslo databázy kokpitu
priamo v texte** (2 riadky s heslom v adrese), súbor je čitateľný pre všetkých (`-rw-rw-r--`) a leží pod
`/opt/customers`, ktoré je pripojené do kokpitu (pravidlo: tajomstvo nesmie byť pod cestami pripojenými
do kokpitu). V gite nie je. Pri kontrole sa riadok dostal do výstupu nástroja pod-agenta (záznam sedenia
na ANDROSe; hodnotu nikto nezobrazil). Návrh: heslo presunúť mimo pripojených ciest a zmeniť —
samostatné rozhodnutie Directora (zmena PROD = reštart kokpitu).

# Nápady — nový nápad s Projektantom, bez projektu (DEV-318)

> **DEV-343 (10.11.2026):** agent v Nápadoch sa volá **Projektant**, Poradca ostáva len pri projektoch. Director: „my
> v kokpite máme už agenta Poradca, a keď rozprávame s chlapcami medzi sebou pojem Poradca je chaotická“. Text nižšie
> vznikol pred premenovaním — kde hovorí o Poradcovi v nápade, myslí Projektanta; po založení projektu rozhovor
> pokračuje s Poradcom projektu. Meno určuje `runner.agent_of` (rozhovor bez projektu = Projektant).

Stav: **schválené Directorom 06.11.2026** („Súhlasím, priprav to a môžeš začať to robiť.“), postavené vo v4.117.0.

## 1. Zámer

Director 06.11.2026: „Návrhy nových projektov sa mi zhromažďujú. Navrhni mi riešenie, aby som mohol tie návrhy robiť
pokiaľ ty pracuješ nad kokpitom.“ — „nezvládne to Poradca? Veď hlavne sa jedná o zber informácii a konzultácie,
hľadanie vhodného riešenia.“ — „Robiť nový projekt pod iným cudzím projektom, je nelogické.“ — „Dobre nazveme
"Návrhy".“ — „Návrh je vinikajúci, schvaľujem s dvoma drobnými úpravami“: položka **nad** Projektmi a nápad môže
skončiť aj **doplnením existujúceho projektu**. Názov položky: Director 06.11.2026 „Porozmýšlaľ som nad názvom "Návrhy" a považujem to za nesprávne a
mätúce“ (bije sa s fázou Návrh) — „Súhlasím, nazveme to Nápady“.

Cieľ: človek v kokpite (Zoltán, Tibor, Nazar) sa s Poradcom poradí nad nápadom ešte pred projektom, Poradca s ním
pripraví zadanie a jedným kliknutím z neho vznikne projekt — alebo požiadavka v existujúcom projekte. Bez Deda.

## 2. Čo kokpit má dnes (zmerané 06.11.2026, v4.116.1)

| čo | stav | čo to znamená pre Nápady |
|---|---|---|
| Rozhovor Poradcu (`poradca_conversations`) | `project_id` NOT NULL; prístup vždy cez projekt (`authz.assert_project_*`) | rozhovor bez projektu dnes neexistuje — treba ho dovoliť |
| Beh otázky (`poradca/sandbox.py`, `runner.py`) | projekt pripojený len na čítanie a je pracovným priečinkom; záznam rozhovoru podľa cesty projektu | nápad potrebuje vlastný prázdny pracovný priečinok |
| Nástroje Poradcu (`tools.py`) | 20 nástrojov; všetky okrem Znalostnej bázy viazané na jeden projekt; zoznam projektov žiadny | Nápady dostanú vlastné nástroje (kapitola 4.3) |
| Pravidlá Poradcu | `templates/poradca-charter.md` + pravidlá typu projektu | Nápady dostanú vlastné pravidlá zo zručnosti Deda `novy-projekt` |
| Náklady | cena pri každej odpovedi; v Nákladoch projektu riadok Poradca (podľa projektu rozhovoru) | nápad bez projektu sa do Nákladov nedostane — kapitola 4.7 |
| Nový projekt (`NewProjectPage.tsx`, `POST /projects`) | meno, slug, typ, prihlásenie, opis, šablóna, AI, porty, CI…; **zákazník vo formulári nie je** | formulár sa dá predvyplniť; zákazník sa zapíše ako dnes na obrazovke Zákazníci |
| Zadanie verzie (`PUT /versions/{id}/zadanie`, `write_zadanie`) | zapíše `customer-requirements.md`; nový projekt má hneď verziu 0.1.0 | zadanie z nápadu pôjde do 0.1.0 rovnako, ako ho dnes posiela Dedo |
| Zásobník (`backlog_service.create`) | požiadavka REQ-N projektu; z odpovede Poradcu tlačidlom „Uložiť do Zásobníka“ (DEV-29) | koniec „Pridať do projektu“ |
| Modul v DEV | kokpit ho založí sám pri prvom tikete projektu (`plane_gateway.ensure_module`, `plane_tickets.module_for`) | pri založení projektu z nápadu ho kokpit založí hneď aj s tiketom |

## 3. Ako to bude fungovať — pohľad človeka

1. **Kde.** Ľavé menu: položka **Nápady** nad **Projekty**, dostupná vždy, bez pripnutého projektu. Na stránke
   Projekty pri „Nový projekt“ aj **„Navrhnúť s Poradcom“** (otvorí nový nápad).
2. **Zoznam.** Pracovný názov, dátum, stav — *Rozpracovaný*, *Zadanie hotové*, *Založený ako <projekt>*,
   *Pridaný do <projekt>* — a cena doterajších odpovedí. **Nový nápad**. Autor vidí svoje, účet admin všetky.
   Nápad sa dá zahodiť (zmizne zo zoznamu, cena ostane započítaná).
3. **Začiatok.** Hrubý nápad textom (aj skopírovaný z Obsidianu) a obrázkami (PNG, JPEG, WebP, GIF — ako dnes
   pri Poradcovi).
4. **Rozhovor.** Poradca najprv pozrie, čo už máme — Znalostnú bázu ICC s právami človeka a prehľad projektov,
   ku ktorým má človek prístup (meno, opis, typ, šablóna, zákazníci, verzie, dokumentácia projektu; nie kód ani
   dáta). Potom sa pýta **po jednej otázke s odporúčaním**: anglické meno a slug, skratka (rozhodne Director),
   rozsah prvej verzie, typ, prihlásenie a šablóna (len to, čo kokpit pri založení naozaj vie — tie isté možnosti
   ako formulár), zákazník, stroj, ľudia a roly, otvorené otázky. Keď zistí, že nápad patrí do existujúceho
   projektu, povie to a odporučí ho. Rozhovor pokračuje aj na druhý deň.
5. **Zadanie.** Poradca napíše zadanie po slovensky v tvare, aký dnes posiela do kokpitu Dedo; na pokyn ho prepíše.
   Nápad sa prepne na *Zadanie hotové*.
6. **Dva konce — klikne vždy človek, Poradca sám nič nezakladá** (len pod posledným zadaním; staršie Poradca prepísal):
   - **„Založiť projekt“** — otvorí formulár Nový projekt predvyplnený z nápadu (meno, slug, opis, typ, prihlásenie,
     šablóna, AI; porty navrhne kokpit z evidencie ako dnes). Po potvrdení kokpit projekt založí ako dnes, do jeho
     verzie 0.1.0 zapíše zadanie a v evidencii DEV založí modul projektu a tiket so zadaním. Tiket nesie druh práce
     (DEV-118) — preto sa Poradca pri zákazníkovi opýta aj, **kto prvú verziu platí** (zákazník = požiadavka, alebo dar
     ICC); bez toho sa projekt z nápadu založiť nedá. Zákazníka zapíše človek ako dnes na obrazovke Zákazníci (formulár
     Nový projekt zákazníka nemá). Stránka projektu po založení povie, kam zadanie išlo a aký tiket vznikol.
   - **„Pridať do projektu <X>“** — zadanie sa uloží do Zásobníka projektu X ako požiadavka REQ-N; do ktorej verzie
     pôjde, rozhodne Director, ako pri každej požiadavke z Poradcu (DEV-29). Ponúkne sa len projekt, ku ktorému má
     človek prístup.
7. **Po konci** sa rozhovor presunie k projektu — v jeho Poradcovi je ako prvý a pokračuje sa v ňom; v Nápadoch
   ostane *Založený ako …* / *Pridaný do …* s odkazom.
8. **Čo Poradca v Nápadoch nesmie** — to isté ako dnes: nič nemení, nevidí ostré inštalácie, trezor, `.env`, kód
   ani dáta iných projektov, nedostane sa na internet.
9. **Diera kokpitu** (napríklad Web projekt na šablóne) — Poradca ju pomenuje a ponúkne tiket na kokpit
   („Založiť tiket“, ako dnes).

## 4. Ako to funguje vnútri

### 4.1 Údaje
- Nová tabuľka `ideas` (v kóde „idea“ — „návrh“ je v kokpite aj fáza Návrh, migrácia 142): autor, výsledok
  (`founded` / `added`), kde skončil (projekt, pri doplnení požiadavka Zásobníka, odpoveď so zadaním, tiket v DEV),
  `concluded_at`, `deleted_at`. Pracovný názov je názov rozhovoru. Stav pre obrazovku sa neukladá dvakrát — odvodí ho
  `backend/services/poradca/ideas.py` (`state`): výsledok → *Založený* / *Pridaný*; dokončená odpoveď so zadaním, ktorá
  sa nepýta → *Zadanie hotové*; inak *Rozpracovaný*. Zadanie sa číta z poslednej takej odpovede (`handoff.project_brief`,
  `handoff.project_addition`).
- `poradca_conversations.project_id` smie byť prázdny; nový stĺpec `idea_id`. Kontrola v databáze: rozhovor má
  projekt alebo nápad, nikdy ani jedno. Po konci nápadu dostane rozhovor projekt (nápad mu ostane ako pôvod).

### 4.2 Beh otázky bez projektu
- Pracovný priečinok nápadu `<poradca_data_dir>/ideas/<id>/`, prázdny, pripojený len na čítanie ako `/ideas/<id>`;
  záznam rozhovoru v tom istom mieste ako dnes (`sessions/<rozhovor>`). Žiadny projekt ani iný priečinok — len vlastné
  dáta Poradcu (v PROD `/opt/data/nex-studio-visual/poradca`) a binárka `claude` — rovnaké oplotenie, rovnaký kontajner, rovnaký strop 3 otázok a 15 minút.
- Stráž (ako `tests/test_poradca_sandbox.py`): zoznam pripojení otázky v nápadu je presne daná množina — bez
  projektu, bez `/opt/projects`, `/opt/customers`, `docker.sock`, Znalostnej bázy na disku.

### 4.3 Nástroje v nápadu
- `znalostna_baza`, `znalostna_baza_dokument` (už dnes len podľa práv človeka);
- nové: `projekty` (prehľad projektov s prístupom človeka — tie isté údaje ako stránka Projekty a Zákazníci),
  `dokumentacia_projektu` (len `docs/` projektu s prístupom; nie kód), `moznosti_zalozenia` (tie isté údaje, ktoré
  formulár Nový projekt číta z `/projects/create-presets`), `zasobnik_projektu` (požiadavky projektu, aby
  „Pridať do projektu“ nezaložilo duplikát).
- Prístup každého nástroja overuje kokpit pre človeka, ktorý sa pýta — rovnako ako obrazovky.

### 4.4 Pravidlá Poradcu v nápadu
`templates/poradca-ideas-charter.md`: postup zručnosti `novy-projekt` (meno po anglicky, otázky po jednej
s odporúčaním, rozsah prvej verzie, čo kokpit vie, zadanie po slovensky, v zadaní len tiket DEV), a dva bloky
odpovede: `<zadanie-projektu …>` (meno, slug, typ, prihlásenie, šablóna, AI, opis + text zadania) a
`<doplnenie-projektu projekt="…">` (text zadania). Stráž: pravidlá menujú každý nástroj nápadu.

### 4.5 Dva konce
- **Založiť projekt:** formulár Nový projekt s `?napad=<id>&odpoved=<odpoveď>` predvyplní polia z bloku; po založení kokpit zavolá
  `POST /api/v1/poradca/ideas/{id}/founded` — zadanie do 0.1.0 (`write_zadanie`), modul a tiket v DEV (`plane_tickets`),
  prepojenie rozhovoru a nápadu. Druhé kliknutie nič nezdvojí; keď prepojenie zlyhá, nápad ponúkne „Dokončiť“.
- **Pridať do projektu:** `POST /api/v1/poradca/ideas/{id}/added` — požiadavka cez `backlog_service.create`, prepojenie.

### 4.6 Rozhovor po presune
Pokračuje v projekte (`--resume` tej istej relácie; pracovný priečinok sa zmení na projekt). Zmerané 06.11.2026:
záznam relácie presunutý pod meno iného pracovného priečinka Claude Code obnoví a model si pamätá, čo bolo povedané
predtým. Záznam rozhovoru leží v priečinku rozhovoru (`sessions/<rozhovor>`) a pripája sa pod menom podľa
aktuálneho pracovného priečinka, takže presun nič nekopíruje. Pri prvej otázke v projekte dostane rozhovor pravidlá
Poradcu projektu (DEV-30 — iný odtlačok pravidiel).

### 4.7 Náklady
Cena pri každej odpovedi a súčet v zozname Nápadov. Po konci sa rozhovor počíta v Nákladoch projektu (riadok
Poradca) — konzultácia je súčasť ceny projektu. Zahodený nápad: cena ostane viditeľná v Nápadoch (filter „zahodené“).

### 4.8 Obrazovky
`Sidebar.tsx` (Nápady nad Projekty), trasy `/napady` a `/napady/:ideaId`, stránka Nápadov zo spoločných častí Poradcu
(`components/poradca/*`), tlačidlá dvoch koncov, predvyplnený `NewProjectPage`, tlačidlo na stránke Projekty.

## 5. Kto smie

Nápad založí, kto smie zakladať projekty (ako Nový projekt, `require_shu_or_above`). Vidí ho autor, účet admin
všetky. „Pridať do projektu“ ponúkne a prijme len projekt, ku ktorému má človek prístup.

## 6. Postup a veľkosť

Jedna verzia kokpitu **4.117.0** (nová funkcia), päť fáz, každá najprv červená skúška, potom kód:

| fáza | čo | môj čas |
|---|---|---|
| 1 | údaje, API nápadov, beh otázky bez projektu + stráž pripojení, skúška `--resume` po presune | ~70 min |
| 2 | nástroje a pravidlá Poradcu v nápadu | ~45 min |
| 3 | dva konce (projekt + zadanie + DEV; Zásobník) | ~45 min |
| 4 | obrazovky a skúšky frontendu | ~75 min |
| 5 | dokumentácia, poznámky k vydaniu, všetky brány, CI (8–19 min), nasadenie, overenie | ~45 min |

**Súhrn: mne ~4,5 h (s bránami a CI); človeku ~5–7 pracovných dní.** Istota stredná: najviac môže pohnúť
kapitola 4.6 (+1 h) a úprava stránky Poradcu na spoločné časti. Naživo overí Director prvým skutočným nápadom
(Dedo sa do kokpitu pod účtom človeka neprihlasuje).

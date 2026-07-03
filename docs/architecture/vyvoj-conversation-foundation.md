# Vývoj ako rozhovor — conversation-foundation (NEX Studio v2)

> Status: **NÁVRH (čaká na revíziu Directora)** · Autor: Dedo · 2026-07-03
> Zastrešujúci dizajn. Fázy = samostatné CR-čka. Vznikol z Director↔Dedo dialógu 2026-07-03 (po
> nex-agents backstage-Dedo zlyhaní). Pod ním: [[project_nex_studio_conversation_foundation]],
> [[feedback_backstage_dedo_test]], `interactive-consultation-design.md` (CR-V2-041),
> `verifikacia-fail-consultation-cr.md` (CR-V2-058 = F0).

---

## 1. Prečo — preskladanie, nie nová funkcia

Vývoj tab je **dashboard, nie rozhovor**: fázová lišta, plán úloh, buttony (Pokračovať/Uprav). Keď je
Manažérovi niečo nejasné, **nemá kde sa agenta opýtať** — beží za Dedom, ktorý pozrie cez zadné dvierka
**mimo projektu**. Pritom AI Agent je **ten istý model ako Dedo** (Opus MAX) — problém **nie je inteligencia,
je to rozhranie.** Appka amputovala rozhovor (agent len „vydaj verdikt / spracuj klik").

**Cieľ = „Dedo na obrazovke".** Tri vlastnosti, alebo nič:
1. **Rozhovor** — obojsmerný kanál, píš hocikedy.
2. **Proaktivita** — agent riziko *vytiahne sám* (non-expert nevie, že sa má spýtať „obíde agent ten hook?").
3. **Rigor** — agent si *overí, než poradí* (ten istý model vie byť rovnako sebavedomo vedľa ako Auditor).

**Akceptačný princíp (backstage test):** vedel by Tibor/Nazar **len z obrazovky** získať info a konať? Ak nie
→ diera. Platí per fáza.

---

## 2. Kľúčové zistenie — ~80 % už existuje

Toto **NIE je nový stack**, je to preskladanie. (Overené 2 research workflowmi, 2026-07-03.)

| Vrstva | Existuje dnes | Kde |
|---|---|---|
| **Transport** — správa = ďalší `--resume` ťah agenta na teplej session | ✅ relay | `relay_manazer_message` orchestrator.py:1979; `POST /pipeline/{v}/relay` |
| **Turn-taking** — front pre správy počas ťahu + jediný pisateľ | ✅ hotové | `_RELAY_QUEUES` :210, `drain_relay_turn` :2028, `_ENGINE_ACTIVE_SESSIONS` :167 |
| **Chat UI** — bublinový, seq-zoradený, autorský prúd + live tail | ✅ `AgentTranscript.tsx` | `frontend/src/components/agent/` (dnes na `/ai-agent`, nie v strede Vývoja) |
| **Trvalá správa** — author/kind/content/payload + WS append | ✅ `PipelineMessage` + `message_added` | pipeline_runner.py:104 |
| **Rozhodnutia v prúde** + agent-sa-pýta-späť | ✅ `DecisionCardStack`, `ask`/`answer`/`decide` | — |
| **Ľudský hlas — precedens** | ✅ konzultačný directive „Manažér je NEŠPECIALISTA — píš ĽUDSKOU rečou" | orchestrator.py:988 |
| **Rigor** | 🟡 CR-V2-058 kritik (schválený návrh, ešte nepostavený) | `verifikacia-fail-consultation-cr.md` |

---

## 3. Cieľový tvar

```
┌──────────────────────────────────────────────┬─────────────────────────┐
│ ✓ Príprava › ✓ Návrh › ● Programovanie › ○ V. │  Plán úloh   52/53 · 98%│  ← lišta = kompaktný pruh
├──────────────────────────────────────────────┤  ▸ 1. Skeleton        ✓ │
│  💬  jednotný prúd rozhovoru s AI Agentom      │  ▸ 2. Autentifikácia  ✓ │  ← pravý stĺpec = plán
│  · agent hovorí ľudsky každý ťah               │  ▸ …                    │
│  · Auditorove nálezy v agentovom hlase (rigor) │  ▸ 12. Oprava po V.   ✓ │
│  · schvaľovacie body inline na bubline         │  ▸ 13. Oprava po V.   ⏳│
│  · rozhodnutia = karty v prúde                 │                         │
├──────────────────────────────────────────────┤                         │
│ [ napíš agentovi…                        ] [→] │                         │  ← jeden composer
└──────────────────────────────────────────────┴─────────────────────────┘
        (žiadny „Terminál (debug)" — preč z Manažérovej plochy)
```

Stred = rozhovor (celý `recent_messages`, nie „posledný artefakt fázy"). Pravý stĺpec = Plán úloh. Fázová
lišta = kontextový pruh. Debug terminál preč (break-glass ostáva len pre Deda/admina mimo tejto plochy).

---

## 4. Ako to funguje

### 4.1 Agentov HLAS (naráция)
Dnes agent produkuje len: efemérne 140-znakové tool-riadky (stratené na reconnect), jednoriadkový `summary`,
a **formálny** `payload.report` (## Dokončené…) — **žiadna bežiaca ľudská naráция.** Pridáme **naráčné pole na
status-blok** (`narration: str`), distinctne renderované (hlas navrchu bubliny, formálny report pod ním).
**Pole, nie nový kind** (§7-D1).

**Reálny edit-set (self-audit — NIE „rides existujúcu cestu zadarmo"):**
- `PipelineStatusBlock.narration: str` — pydantic pole; JSON-schema grammar + `_validate_block` ho nesú
  **zadarmo** (schéma = `model_json_schema()`).
- **jeden explicitný payload-zápis** v `_record_message` (orchestrator.py:2244-2301 vypisuje polia **ručne**,
  NIE `model_dump()` — bez toho riadku sa `narration` ticho zahodí).
- **jeden render-riadok** v `AgentTranscript.tsx` (dnes renderuje `payload.report`, nie `payload.narration`).
- **directive** paralelne k :988 do Príprava/Návrh/Programovanie briefov: *„Začni krátkou naráciou po ĽUDSKY
  (nešpecialista, bez žargónu): čo ideš robiť a prečo; na konci čo si zistil a čo ďalej. Ak vidíš riziko, povedz
  to hneď."*

Platí len pre **AI-Agentove fázové ťahy cez `invoke_agent`** (nie engine-interné sub-passy `_invoke_plan_pass`
/ CR-058 kritik, ktoré status-blok obchádzajú).

### 4.2 Rigor v hlase
CR-058 kritik už vyrobí `fix_critique{verdict, corrected_scope, why}` — **ľudskou rečou** (CR-058 §5), autorom
`auditor→manazer`. **Pozor (self-audit):** na non-fast_fix Verifikácia-FAIL **niet AI-Agentovho ťahu** (verdikt
píše Auditor a `_settle` hneď pauzuje) — refutáciu teda **nehovorí AI-Agent v novom ťahu**, je to
**Auditorom-autorský záznam.** F1 ho preto len **čitateľne vyrenderuje v prúde** (Auditorova bublina: „Auditor
navrhol X; preveril som — je to falošná hranica, lebo…; odporúčam Y") — **žiadny nový ťah, žiadna cena.**
Naráция „v agentovom hlase" (§4.1) platí pre **jeho vlastné fázové ťahy** (Príprava/Návrh/Programovanie).
> **Invariant:** hlas/záznam **vysvetľuje**, engine **garantuje odporúčanie konštrukciou** (CR-058 §2).
> Bezpečnosť rozhodnutia sa **NIKDY neviaže na text narácie.**

### 4.3 Proaktivita
Lacná (v scope): agent vytiahne riziko **vo svojej per-ťah narácii** (rides existujúce ťahy, skoro zadarmo).
Pravá (odložená, F5): nevyžiadaný „mid-idle" štuchanec, keď nič nebeží — dnes niet mechanizmu emitnúť správu bez
dispatchu; treba nový trigger s reálnou cenou.

### 4.4 „Píš hocikedy" = FRONT, nie prerušenie (čestné)
Headless `claude --resume` ťah sa **nedá prerušiť v polovici** — architektúra. `relay_manazer_message`: settled
→ správa je ďalší ťah hneď; in-flight → **front** (`_RELAY_QUEUES`), `deferred=True`, drain na hranici ťahu.
Composer je **vždy otvorený**; úloha je **čitateľnosť** (optimistická „vo fronte" bublina + pruh „dorazí po
tomto ťahu/úlohe"), nie preempcia. Programovanie má kooperatívnu pauzu na hranici úlohy; jednofázové kroky
(Príprava/Návrh/Verifikácia) mid-turn checkpoint nemajú — **nesľubovať okamžité „stop".**

**Čestne navyše (self-audit):** pri **auto-postupujúcej** fáze (dial nezastavuje) sa správa poslaná počas behu
drainne **až po tom, čo fáza už skončila a postúpila** — teda nielen s latenciou, ale **možno neskoro na
usmernenie tej fázy.** Na rozdiel od Deda vie **dial prejsť hranicu fázy bez teba.** Dve poistky: (i) **dial JE
tá kontrola** — chceš usmerniť každú fázu? nastav zastavujúcu úroveň; (ii) čitateľnosť — ukázať, kam správa
dopadne vzhľadom na fázu.
> **Rámovanie:** *v rámci ťahu* je to **presne ako Director↔Dedo teraz** — tvoja správa sa spracuje, keď
> dokončím ťah; nie je to downgrade, **JE to práca s Dedom.** Jediný rozdiel je dial (vyššie).

### 4.5 Schvaľovacie body v prúde
`determine_available_actions` (:458) ostáva jediná autorita — len sa **vynáša per-správa**, nie globálnou
lištou. Agentov ťah skončí naráciou, čo **položí bod ako otázku** („Špecifikácia hotová — schváliť a ísť do
Návrhu?"); potvrdenie je **inline quick-reply na bubline** (alebo Decision Card pre rozhodnutie).
`uprav/ask/answer` = **len napíš** do jednotného composera. Fázovo-globálne verby (`pause`, `overit_znovu`)
ostanú v **tenkom trvalom riadku** (nie na bubline).

**Pozn. (self-audit):** verby dnes **NIE sú** na payloade správ — žijú len v board-global `available_actions`
(pipeline.py:103). „Verby per-správa" = **FE re-projekcia** tej istej množiny na poslednú ustálenú bublinu +
**presun spresňujúcich pravidiel** (skry `schvalit` pri prázdnom pláne, disable v Programovaní podľa
`build_open_findings`/`all_tasks_done`) z route/`PipelineActionBar`. Nie nová engine práca, ale **viac než „len
render na bubline".**

---

## 5. Fázovanie (CR-čka)

| Fáza | CR | Čo | Riziko | Vrstva |
|---|---|---|---|---|
| **F0** | CR-V2-058 | Kritik / rigor (schválené: always-on `new_version`). *+ nex-agents push-gate fix prejde cezeň.* | stred | backend |
| **F1** | CR-V2-059 | **Hlas** — naráčné pole + directive; render v existujúcom `AgentTranscript` na `/ai-agent`. Vrátane „narác kritikovu refutáciu" na V-FAIL. | **nízke** | backend-only |
| **F2** | CR-V2-060 | **Rozhovor do stredu Vývoja — výmena IA, nie len relayout** (§5.1): retire `ExchangePanel`/`PhaseArtifact`; `AgentTranscript`(recent_messages) → stred; `TaskPlanPanel` → pravý stĺpec; kompaktná lišta; **debug terminál preč**. | **stred–vyššie** | FE (IA) |
| **F3** | CR-V2-061 | **Jednotný composer + body v prúde** — zlúč `AgentInputBox` + `PipelineActionBar`; verby per-správa inline (§4.5); `DecisionCardStack` v prúde; čitateľný „front" UX. | stred | BE+FE |
| **F4** | CR-V2-062 | *(voliteľná vidlička)* **Rozhovor pred buildom** — version-nezávislý `Conversation` entity + relay bez pipeline + zoznam rozhovorov. Jediný kus s **reálne novým inžinierstvom.** | veľké | BE+FE |
| **F5** | — | *(mimo F0–F4)* Pravá proaktivita (nevyžiadaný mid-idle štuchanec) + kooperatívne checkpointy pre mid-turn „stop". Nový trigger s cenou / nový stop-mechanizmus. **Odložené, nie v pláne.** | veľké | BE |

**F0–F3 doručia celý viditeľný dizajn** (agent hovorí, body v prúde, plán vpravo, terminál preč) a **reusujú
celý relay/queue/transcript stack.** F4 nefrontloadovať — je to jediná časť, čo pridáva novú schému/entitu.

### 5.1 Čo F2 retiruje / re-homuje (IA strata — self-audit)

Dnešný stred je **artefakt-centrický** (`ExchangePanel`/`PhaseArtifact` — per-fáza taby, „posledný artefakt
fázy"), NIE prúd. Prechod na jednotný `recent_messages` prúd musí **vedome presunúť** štyri veci:
1. **Live status/decision banner** (Bell + `bannerText`) → stavový riadok v prúde / na ustálenú bublinu.
2. **AuditorUpfrontReview pin** nad Návrh doc → ako bublina/karta v prúde.
3. **Efemérny `PipelineActivityFeed` live tail** → tail poslednej agentovej bubliny (kým pracuje).
4. **Prezeranie DOKONČENEJ fázy ako tichý trvalý záznam** — **najväčšia strata:** jednotný prúd je cross-fáza
   chronologický firehose; per-fáza review zmizne. Riešenie: **fázové kotvy/filter v prúde** (klik na fázovú
   lištu → skoč/filter na jej správy), nech sa „Špecifikácia" / „design doc" / „Auditor verdikt" dajú prečítať
   ako celok. Bez toho F2 stratí čitateľnosť hotovej práce.

---

## 6. Invarianty a čestné tvrdé pravdy

1. **Front, nie prerušenie** (§4.4) — architektúra, nie chýbajúca funkcia. UX = čitateľnosť latencie.
2. **Rigor ≠ dôvera v prózu** (§4.2) — engine garantuje odporúčanie konštrukciou (CR-058 §2); hlas len
   vysvetľuje. Bezpečnosť sa nikdy neviaže na text narácie.
3. **Kvalita narácie je directive-závislá a per-ťah neoveriteľná** — mitigácia je tón+audience pravidlo (ako
   dnes konzultácia) + fakt, že rozhodovacia bezpečnosť je v engine, nie v texte.
4. **F0 je prerekvizita rigoru** — bez CR-058 nemá agent overenú refutáciu, ktorú by v hlase povedal.

---

## 7. Otvorené rozhodnutia (postupne, keď doň prídeme)

S odporúčaniami:
1. **D1 — naráčné POLE na status-bloku vs nový `narration` KIND.** *Odporúčam pole* (žiadny nový emit-path,
   žiadny **AI-Agentov fázový ťah** bez narácie). — moje rozhodnutie, značím.
2. **D2 — F4 ambícia:** version-nezávislý rozhovor pred buildom, alebo ostať build-anchored? *Bigger value, ale
   jediné reálne nové inžinierstvo.* **Vedomá vidlička Directora — až po F2/F3.**
3. **D3 — hĺbka proaktivity:** per-ťah naráция (v scope) vs nevyžiadaný mid-idle štuchanec (F5, drahšie).
   *Odporúčam per-ťah teraz.*
4. **D4 — granularita verbov:** ktoré verby idú inline na bublinu vs do tenkého trvalého riadku (pause,
   overit_znovu). *Blessed zoznam pri F3.*
5. **D5 — trvalý pravý stĺpec:** Plán úloh musí rozumne vyzerať aj v Príprave/Verifikácii (prázdny stav
   „plán vznikne v Návrhu"). *Odporúčam vždy prítomný s prázdnymi stavmi.* Pozor (self-audit): dnes je
   interaktívny plán-strom vnorený v `ExchangePanel` Návrh-splite (`taskPlanSlot`, CockpitPage.tsx:176) — presun
   do pravého stĺpca musí zachovať **editovanie plánu v Návrhu.**

---

## 8. Akceptácia (backstage test, per fáza)

- **F1:** Manažér v transcript **rozumie** čo agent robí a prečo (naráция na AI-Agentových ťahoch); na V-FAIL
  sa **čitateľne vyrenderuje Auditorom-autorský kritik-záznam** (prečo je návrh zlý) — nie surový verdikt (bez
  nového ťahu).
- **F2:** Vývoj tab má rozhovor v strede, plán vpravo, žiadny debug terminál; Manažér nemusí nikam „bežať".
- **F3:** Manažér **koná priamo v prúde** — schváli/usmerní/rozhodne inline, píše hocikedy (front čitateľný);
  žiadna oddelená lišta buttonov.
- **Celé:** non-expert (Tibor/Nazar) prejde celým buildom **bez** zákulisného Deda.

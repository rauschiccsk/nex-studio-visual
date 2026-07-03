# CR-V2-058 — Verifikácia-FAIL: deliberovaná konzultácia + adversariálne preverenie navrhnutej opravy

> Status: **NÁVRH (čaká na schválenie Directora)** · Autor: Dedo · 2026-07-03
> Nadväzuje na `interactive-consultation-design.md` (CR-V2-041) — jeho Fáza 2 pre Verifikáciu **plus**
> genuinely nová časť (preverenie opravy), ktorá v pôvodnom §7/§8 nebola.
> Draft prešiel 2-recenzentským adversariálnym self-auditom (soundness + implementovateľnosť); tento dokument
> už obsahuje jeho must-fixy (najmä: invariant vynútený **konštrukciou**, nie ručne per-cesta).

---

## 1. Problém — živý prípad + backstage test

**Živý prípad (nex-agents re-verify, 2026-07-03).** Auditor po „Over znova" vydal reálny FAIL (git push gate
neplatí pre `full_auto`) a **navrhol opravu — pre-push hook**. Ten návrh je **falošná hranica**: agent v móde
`bypassPermissions` ho obíde cez `git push --no-verify`. Na obrazovke by non-expert Manažér (Tibor/Nazar) videl
len pozastavený build s **„Pokračovať" / „Uprav"** → klikol by „Pokračovať" a **vydal falošnú bezpečnostnú
opravu**. Správnu alternatívu (default `write_commit`) som odvodil len cez zákulisný 4-agentový workflow + živé
`build_permissions()` + znalosť permission modelu — **nič z toho nie je na obrazovke.**

**Backstage-Dedo test (záväzné kritérium).** Vedeli by Tibor/Nazar (a) **získať** podklad a (b) **napísať**
reakciu — **len z obrazovky**? Ak nie → **oprav NEX Studio**. Tu je odpoveď dnes dvakrát NIE.

**Dve previazané diery (obe backend-only, overené proti kódu):**

- **Diera A — prezentácia.** Deliberovaná karta naskočí **až pri vyčerpaní** slučky (`iteration >=
  AUDITOR_LOOP_MAX = 5`, CR-V2-054). Bežný **PRVÝ FAIL** dá `status='paused'` + holé `{pokracovat, uprav}`
  (`_settle_verifikacia_verdict` :4416–4422).
- **Diera B — podstata (nosná).** Auditorov `proposed_fix` (:4597) **nie je nikdy adversariálne preverený** —
  tečie verbatim cez `_latest_verifikacia_fix_scope` (:4831) do fix-tasku (:4408). **Nálezca navrhne rozsah a
  ten istý nálezca prehodnotí výsledok.** Aj pekná karta by odporučila nepreverenú (falošnú) opravu. **A bez B
  len zabalí zlú opravu do peknej karty.**

---

## 2. Cieľ + nosný invariant (vynútený KONŠTRUKCIOU)

Pri **každom** Verifikácia-FAIL dostane Manažér **sebestačnú** kartu: ľudské vysvetlenie čo/prečo zlyhalo +
**nezávisle preverené** možnosti + odporúčanie, jedno rozhodnutie naraz — **rozhodnuteľné len z obrazovky.**

> **INVARIANT: „Spustiť pripravenú opravu" je ODPORÚČANÁ iba vtedy, keď existuje pozitívny záznam kritika
> (`fix_critique.verdict ∈ {accept, narrow}`). Inak je demote-nutá/skrytá a odporúčaná je „Usmerniť opravu".**

**Kľúčová oprava po self-audite:** invariant sa NEDRŽÍ ručne per-cesta (self-audit našiel 3 diery — manuálna
verdikt-vetva, fail-open, nešpecifikovaný scope override). Drží sa **konštrukciou**: jeden zdieľaný staviteľ
karty `_build_fix_consultation(...)` číta **posledný `fix_critique` záznam pre dané kolo**; keď chýba alebo je
`reject` → `accept_fix` je demote-nutá a `guide` je odporúčaná. Staviteľ **assertuje práve jednu `recommended`**
(engine-karty neprechádzajú `_validate_block`, tak si to vynúti sám). Tým každá ne-preverená cesta (manuálna,
fail-open, skip) fail-safe **bez** zvláštneho vetvenia.

---

## 3. Čo sa reusuje (CR-V2-041) — na FE nič nové

Celý stack kariet ostáva nezmenený: `ConsultOption/ConsultDecision/ConsultationBlock`
(`pipeline_status.py:214–256`), verb `decide` (`orchestrator.py:5962–6026`, SEQ-scoped), `determine_available_actions`
→ `{decide, ask}` na `decision_needed`, a **`DecisionCardStack.tsx`** (source-agnostický, mapuje pole-za-pole).
Žiadny nový verb, žiadna zmena schémy kariet, žiadna migrácia. **CR je čisto backend + PROD rebuild.**
(Pozn.: karty stavia engine v Pythone ako exhaustion-karta :4348 — tá **neprechádza** `_validate_block`, preto
§2 assert v staviteľovi.)

---

## 4. Časť A — deliberovaná karta na PRVOM FAIL

**Kde:** `_settle_verifikacia_verdict`, non-fast_fix vetva prvého FAILu (`:4416–4422`). Ponechaj
`_ensure_verifikacia_fix_task` (:4408), `is_regate` + **`iteration += 1`** (:4409–4410) a
`current_stage='programovanie'` (:4411) — iterácia sa bumpne **tu** (raz za FAIL-kolo), takže slučka ostáva
ohraničená a `accept_fix` už **nebumpuje znova**. Namiesto `status='paused'` postav kartu cez zdieľané
`_build_fix_consultation(...)`, zaznamenaj `kind='consultation'` (author=system→manazer, stage=verifikacia),
nastav `status='blocked'`, `block_reason='decision_needed'`. `fast_fix` si **ponecháva** auto-dispatch (:4415) —
ľahká linka bez karty (D3).

**Karta** — **`source='verifikacia_fix'`** (odlíšené od exhaustion `verifikacia_fail`, aby sa v decide handleri
nezrazili — self-audit našiel kolíziu na hardcoded key `verifikacia_fail_next` :6017), jeden bod
`key='verifikacia_fix_next'`:

| Možnosť | Odporúčaná? | `decide` → apply |
|---|---|---|
| **Spustiť pripravenú opravu** (`accept_fix`) | iba ak `fix_critique.verdict ∈ {accept, narrow}` | `_begin_dispatch` na už-materializovaný fix-task (stage=programovanie ho vezme cez `get_next_todo_task`) — **žiadny 2. task, žiadny 2. bump** (D6) |
| **Usmerniť opravu** (`guide`, `allow_free_text`) | inak (chýbajúci/`reject` kritik) | `_route_manazer_fix_to_ai_agent(comment=free_text)` (:4696) |
| **Zatiaľ podržať** (`hold`) | — | **re-block bez konzumácie bodu** (ako medziklik) — karta **ostáva** action-surface; Manažér vyberie accept_fix/guide neskôr. **Žiadny dead-end, žiadny sľub „Uprav"** (na `decision_needed` sa `uprav` neponúka — self-audit) |

**decide handler** (`:6012`): pridaj vetvu `source=='verifikacia_fix'` (popri existujúcej `verifikacia_fail`),
kľúčovanú na `verifikacia_fix_next`. **Účtovanie iterácií (D5):** bump je v settle (card-build); `accept_fix`
resumuje bez ďalšieho bumpu; ľudský `guide` resetuje na 0 (`_route_manazer_fix_to_ai_agent` :4725) — vedomé
(človek v slučke). *Čestne (self-audit): opakovaný `guide` je human-driven neohraničená slučka — akceptovateľné,
lebo Manažér v nej vedome je.*

---

## 5. Časť B — adversariálne preverenie navrhnutej opravy (nosná)

**Kde:** `_run_verifikacia_round`, vetva FAIL, v čistom šve **:4603–4605** (po zázname `kind=verdict` :4602,
pred `_settle` :4606). `runtime_floor_red` je tu v scope → **skip kritika pri engine-červenom FAILe** (D4:
mechanická podlaha JE pravda).

**Invokácia (self-audit fix — NIE cez status-schému).** `invoke_agent_with_parse_retry` je hardwired na
`PIPELINE_STATUS_JSON_SCHEMA` (verdict:bool + findings + proposed_fix) — tvar `{accept,narrow,reject}` tam
nesadne (ParseFail). Preto **dedikovaná úzka cesta ako `_invoke_plan_pass` (:2528):** nový pydantic model
`FixCritique { verdict: Literal["accept","narrow","reject"]; corrected_scope: str = ""; why: str }` + vlastný
`json_schema` + parser. Kritik = **nezávislá `claude` session** `role=AUDITOR_ROLE` (reálna nezávislosť,
finder/fixer/critic split), brief z nového **`_fix_critique_directive`**.

**Directive (self-audit fix — musí niesť permission model fixera, inak chytí falošnú hranicu len náhodou):**
brief EXPLICITNE povie, že (i) opravné kolá bežia **bez dozoru** v `bypassPermissions`/`full_auto` móde; (ii)
zoznam anti-vzorov — *git pre-commit/pre-push hook ako hranica (obísť `--no-verify`); client-side/advisory guard
vs. enforced-by-construction default; oprava symptómu nie príčiny; „self-confirming" test, čo len zazelená
smoke*; (iii) otázku: **je navrhnutá hranica vynútená konštrukciou, alebo len hook/guard, ktorý nedozorovaný
agent obíde?** Rozšírenie CR-V2-053 „REFUTUJ, NEPOTVRDZUJ" (:1188) mierené na **LIEK**, nie build.

**Perzistencia + read-precedence (self-audit fix — inak surový fix aj tak dobehne do tasku).** Kritik zapíše
**append-only** záznam `fix_critique={verdict, why, corrected_scope}` (author=auditor→manazer, stage=verifikacia)
**pred** `_settle`. Dve čítania ho potom uprednostnia:
1. `_latest_verifikacia_fix_scope` (:4831) sa rozšíri: keď existuje `fix_critique` novší než verdikt a má
   `corrected_scope`, vráti **ten** (nie surový `proposed_fix`). Manažér-`return` precedencia drží (neskorší
   `return` má vyšší seq). Tým `_ensure_verifikacia_fix_task` (:4408, v settle) materializuje **preverený** rozsah.
2. `_build_fix_consultation` číta ten istý `fix_critique` → odporúčanie karty (accept/narrow → `accept_fix`
   s (opraveným) rozsahom v detaile; `reject` → `accept_fix` skrytá, `guide` odporúčaná, `why` = koreň je inde).

**Fail-open (self-audit fix — NIE fallback na paused).** Ak kritik ParseFail/timeout → **žiadny `fix_critique`
záznam** → staviteľ karty defaultne `accept_fix` demote + `guide` odporúčaná (nie návrat na `paused`, ktorý by
zase odhalil surové „Pokračovať"). Karta sa postaví vždy; nikdy sa nevraciame do stavu s jednoklikom na
nepreverenú opravu.

---

## 6. Rozhodnutia

**Spravené (kvalita-first):** D1 engine-staví-kartu (deterministické); D3 `fast_fix` auto; D4 skip pri
`runtime_floor_red`; D5 bump v settle, `guide` reset; D6 `accept_fix` = ten istý task; D7 fail-open → karta
s `guide` (nie paused); D8 tvar karty (§4); invariant konštrukciou (§2). Manuálna verdikt-vetva
(`apply_action verdict` :6076) beží bez kritika → žiadny `fix_critique` → karta odporúča `guide` (drží auto).

**Jediné rozhodnutie pre teba (cena):** kritik je **+1 metrovaný Auditor ťah na každý non-fast_fix FAIL**.
*Odporúčam always-on pre `new_version`* — kritik JE bezpečnostná sieť proti falošným hraniciam; gejtovať ho
kvôli tokenom znovu zavádza presne riziko, ktoré opravujeme (memory: „always-on refute critic"). Cena je reálna,
chcem tvoj vedomý súhlas.

---

## 7. Akceptačné kritériá

1. **Backstage test (HLAVNÁ brána).** Non-expert **len z obrazovky** (a) pochopí čo/prečo zlyhalo ľudskou
   rečou, (b) uvidí **nezávisle preverené** možnosti + dôveryhodné odporúčanie, (c) rozhodne správne **bez**
   zákulisia.
2. **Invariant §2 — deterministicky (nie „LLM vždy chytí").** Test: keď **nie je** pozitívny `fix_critique`
   (chýba / `reject`), karta **nikdy** neoznačí `accept_fix` ako recommended (engine-logika, deterministické).
   *Toto je ostrá brána — nie „kritik vždy odhalí hook" (§8).*
3. **Kritik-directive pokrýva anti-vzory** (permission model + hook/`--no-verify`) — *should*: zvyšuje šancu na
   reject/narrow pri falošnej hranici; nie je garantované jedným ťahom.
4. **§6.1 kritériá** (FE už hotové): ⛔ blocker banner, ľudský jazyk, explicitná otázka + čo robí každé
   tlačidlo, **skutočná** blokujúca správa (najvyššie `seq`). `hold` nesmie spraviť dead-end.
5. `fast_fix` nezmenené; engine-červený FAIL vysvetlený bez kritika.

---

## 8. Čestné obmedzenia

Jedno kritické kolo **zdvihne latku**, ale **nie je záruka** — nex-agents prípad som chytil 4-agentovým
workflowom + doménovou znalosťou. Bezpečnosť tu nedrží na tom, že „kritik vždy odhalí" (nedrží), ale na tom, že
**bez pozitívneho kritika sa oprava NIKDY neodporučí** (§2, deterministické) + Manažér **vidí deliberáciu a
vyberá**. Aj keď sa kritik zmýli smerom k `reject`/`narrow`, Manažér dostane lepší štart než dnešné slepé
„Pokračovať". Budúce spevnenie (mimo CR): eskalovať kritika na **viac-hlasý refute panel** pre vysoko-rizikové
nálezy. Opakovaný human `guide` je neohraničená (vedomá) slučka.

---

## 9. Testovací plán

**Backend (plný `pytest` — zdieľaný orchestrátor/status):**
- prvý FAIL non-fast_fix → `block_reason='decision_needed'` + `kind='consultation'` (nie `paused`);
- **invariant deterministicky:** žiadny/`reject` `fix_critique` → `accept_fix` NIE recommended (`guide` je);
  `accept`/`narrow` → `accept_fix` recommended; staviteľ vždy **práve jedna** recommended (assert);
- `_latest_verifikacia_fix_scope` uprednostní `corrected_scope` pred surovým `proposed_fix`; manazer-`return`
  stále vyhráva;
- `decide accept_fix` → resume **toho istého** tasku (žiadny 2., žiadny 2. bump); `guide`→route; `hold`→re-block
  bez dead-endu (karta ostáva);
- source/key: `verifikacia_fix` karta sa nezrazí s exhaustion `verifikacia_fail` (`:6017` handler);
- fail-open (kritik ParseFail) → karta s `guide` (NIE paused); `fast_fix` nezmenené; `runtime_floor_red` skip;
- manuálna verdikt-vetva (:6076) → karta odporúča `guide` (bez kritika);
- účtovanie iterácií (bump v settle raz/kolo, `guide` reset) → slučka ohraničená.

**FE:** žiadne zmeny (DecisionCardStack pokrýva render); **backend deploy** PROD v2.

---

## 10. Rozsah a nasadenie

**Jeden CR (A + B previazané)** — B robí odporúčanie A dôveryhodným; oddelene A nesplní backstage test. Nasadiť
PROD v2 paralelne. **Live-validácia = odložený nex-agents push-gate fix prejde CEZ tento tok:** Over znova →
FAIL → kritik preverí pre-push-hook návrh → (ak reject) karta odporučí **Usmerniť** s kritikovým „prečo je zlý"
v texte → Manažér vyberie. *Čestne:* karta nezaručí, že Manažér trafí presne `write_commit` — ale namiesto
slepého odklepnutia falošnej opravy dostane preverené možnosti + dôvod, prečo je Auditorov návrh zlý; to je
skok od „nerozhodnuteľné" k „rozhodnuteľné". Tým sa uzavrie diera aj jej prezentácia.

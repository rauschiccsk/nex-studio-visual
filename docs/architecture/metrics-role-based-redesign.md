# NEX Studio — Metrics Redesign: Role-Based Agent-vs-Human Model (FINAL DESIGN)

> READ-ONLY design. No files modified. Locked model followed exactly. Honest-by-construction preserved and tightened at every layer. Maximum reuse of the existing E5/WS-D layer; cited file:line for every change. All 16 adversarial-review issues addressed (valid fixes folded in; #16 flagged to Designer).

---

## 0. Princíp a invariant

Jediná reprodukovateľná **BÁZA** pre obe strany = **všetky tokeny (IN+OUT, vrátane retries/failed)** per **rola-pôvodu** per verzia. Z tej istej bázy:
- **Agent** strana: tokeny × API cena (per model) = agent cost; agent wall-clock − idle = agent active time.
- **Human** strana: tokeny × per-role kurz (tokens→min) = human-time; human-time × per-role mzda = human cost.

Symetria robí porovnanie kredibilným: agentova plná spotreba (vrátane jeho zápasov) vs ľudský čas kalibrovaný na plný ľudský proces (čítanie + premýšľanie + produkcia + chyby + rework). Idle je samostatná wall-clock metrika, **nie** súčasť token→time výpočtu. Každá hodnota závislá od nenakonfigurovaného vstupu = `None`, nikdy fabrikované číslo.

**Kritická korekcia oproti pôvodnému návrhu (review #1):** atribúcia musí byť **rola-pôvodu (`metrics_role`), nie autor-záznamu (`msg.author`)**. Capture layer NIE JE bez zmeny — vyžaduje jeden nový payload kľúč `metrics_role` na fold/seed miestach, inak worker tokeny pretekajú do `coordinator`/`system` bucketov a per-role split je nesprávny a hrateľný.

---

## 1. Dáta / agregácia — všetky tokeny per rola-pôvodu per verzia + kumulatívne

### 1.1 Capture layer — JEDNA cielená zmena (review #1, #12)

`_DispatchMetrics` (`orchestrator.py:85-123`) akumuluje IN+OUT+wall-clock+`model` naprieč parse-retry re-emisiami jedného turnu; `attempts`+`duration` vždy, **tokeny len keď `usage is not None`** (`:106-113`). `usage_payload()` vracia `None` (nie fake nuly) keď `saw_usage=False` (`:117`). Toto je správne a NEMENÍME.

**Problém (overené):** autor-záznamu ≠ rola-pôvodu na troch miestach:
- `_coordinator_relay_engine_failure` (`orchestrator.py:1530-1536`): `metrics=_seed_metrics_from_failure(failed)` pred-naplní **workerove** stratené tokeny do `role="coordinator"` relay turnu → workerove tokeny zaznamenané pod `author="coordinator"` (line `1215` `author=role`).
- Build auto-fix return (`orchestrator.py:5117-5134`): zlyhaný Implementer pokus končiaci ParseFailure má tokeny pripojené cez `**_failure_metrics_payload(result)` na `author="system"` return správu → zlyhané Implementer pokusy padnú do (vylúčeného) `system` bucketu → **undercount Programátora**, presný opak locked "retries/failed v báze".
- `verify_done` / `_coordinator_relay` (`:1948`, `:2099`) bežia korektne ako coordinator — to je v poriadku — ale v kombinácii so seed-foldom Koordinátor riadok absorbuje cudzie struggle tokeny.

**Fix — `metrics_role` na fold/seed miestach (rola-pôvodu, nie autor-záznamu):**

Pridať nový voliteľný payload kľúč `metrics_role` do WS-D `usage` payloadu. `extra_payload` mechanizmus už existuje (používa `is_director_brief` `:1552`, `is_synthesis` `:1673`) — `metrics_role` jazdí po tom istom kanáli:

```python
# _failure_metrics_payload / _seed_metrics_from_failure — pri seedovaní z workerovho ParseFailure
# označ WORKEROVU rolu, nie autora-záznamu:
#   seed-fold (1530): extra_payload={"is_director_brief": True, "metrics_role": failed_role}
#   build fail (5121): payload={..., **_failure_metrics_payload(result), "metrics_role": "implementer"}
```

`failed_role` = dispatchovaná rola workra, ktorý vyrobil `failed` ParseFailure (Implementer/Designer/Auditor — caller `_coordinator_relay_engine_failure` ju pozná z kontextu volania, je to `stage`→actor cez `STAGE_ACTOR :208`). Pre build auto-fix je vždy `"implementer"`.

Agregácia potom grupuje podľa `usage.metrics_role or msg.author` (viď §1.2). Kde žiadny seed/fold nie je (drvivá väčšina turnov), `metrics_role` chýba → fallback na `msg.author` = pôvodné správanie. **Žiadny dopad na úspešné turny; opraví len 3 leak miesta.**

**Caveat do §1.1 (review #12):** retry **token** capture je best-effort — retried re-emit s usage-less envelope pridá 0 tokenov, hoci je to reálny platený pokus; ale jeho **wall-clock je vždy započítaný** (`attempts`+`duration` sa foldujú vždy, `:106-107`). Wall-clock je teda garantovane-úplný retry signál; tokeny best-effort. Preto sa `parse_attempts` (`timing_payload :123`) povrchne zobrazí na per-role riadku ako "rework evidence" (§4.3) — reviewer, ktorý diffne attempts vs token-delta, uvidí vysvetlenie, nie "neúplnú bázu".

### 1.2 Agregácia — rola-pôvodu + model na primárnu os

`UsageTotals` (`pipeline_metrics.py:29-42`) rozšíriť o per-model rozpad (kvôli per-model cene), nie nahradiť:

```python
@dataclass
class ModelTokens:
    input_tokens: int = 0
    output_tokens: int = 0

@dataclass
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0
    messages: int = 0
    parse_attempts: int = 0                       # NEW: rework evidence (review #12)
    by_model: dict[str, ModelTokens] = field(default_factory=dict)   # NEW: per-model split

    def add(self, *, input_tokens, output_tokens, duration_seconds, messages=1,
            parse_attempts=0, model: str | None = None) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.duration_seconds += duration_seconds
        self.messages += messages
        self.parse_attempts += parse_attempts
        key = model or "_unknown"
        mt = self.by_model.setdefault(key, ModelTokens())
        mt.input_tokens += input_tokens
        mt.output_tokens += output_tokens

    def merge(self, other: "UsageTotals") -> None:                   # NEW: cumulative incl. by_model
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.duration_seconds += other.duration_seconds
        self.messages += other.messages
        self.parse_attempts += other.parse_attempts
        for m, mt in other.by_model.items():
            dst = self.by_model.setdefault(m, ModelTokens())
            dst.input_tokens += mt.input_tokens
            dst.output_tokens += mt.output_tokens
```

**Nová primárna agregačná funkcia** (vedľa `aggregate_pipeline_usage`, nie namiesto — viď §6) v `pipeline_metrics.py`:

```python
def aggregate_usage_by_role(db, version_id) -> dict[str, UsageTotals]:
    """Single reproducible base: all metered messages grouped by ROLE-OF-ORIGIN
    (usage.metrics_role or author), token split by model, parse_attempts summed.
    Identical scan rule as aggregate_pipeline_usage (counts any payload bearing
    usage OR timing — incl. 0-token/real-wall-clock failed turns), so
    retries/failed attempts are in the base by construction."""
    by_role: dict[str, UsageTotals] = {}
    for msg in db.execute(select(PipelineMessage)
                          .where(PipelineMessage.version_id == version_id)
                          .order_by(PipelineMessage.seq.asc())).scalars():
        payload = msg.payload or {}
        if "usage" not in payload and "timing" not in payload:
            continue
        usage = payload.get("usage") or {}
        timing = payload.get("timing") or {}
        role = usage.get("metrics_role") if isinstance(usage.get("metrics_role"), str) else msg.author
        by_role.setdefault(role, UsageTotals()).add(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            duration_seconds=float(timing.get("duration_seconds") or 0.0),
            parse_attempts=int(timing.get("parse_attempts") or 0),
            model=(usage.get("model") if isinstance(usage.get("model"), str) else None),
        )
    return by_role
```

Skenovacie pravidlo (count payload s `usage` ALEBO `timing`) je byte-identické s `aggregate_pipeline_usage:97`, takže timing-only failed turny ostávajú v báze. Toto je `metrics._usage_by_role` (`:51-66`) povýšené o `metrics_role`, `model`, `parse_attempts` a presunuté do data layeru.

### 1.3 Kumulatívne per rola (GAP — pridať, review #6)

Dnes `_usage_by_role` je len per-verzia (`metrics.py:144-194` sumuje len version-grand-total do `cumulative`). Pridať per-role kumulatív v `compute_project_metrics`:

```python
cumulative_by_role: dict[str, UsageTotals] = {}
for version in versions:
    for role, t in aggregate_usage_by_role(db, version.id).items():
        cumulative_by_role.setdefault(role, UsageTotals()).merge(t)
```

**None-safe kumulatívne ROI (review #6):** kumulatívne `eur_saved`/`m_cheaper` sa NESMÚ počítať ako `Σ(human_cost − agent_cost)` per verzia (verzia s `human_cost=None` by sa tichom spôsobom brala ako 0). Namiesto toho: kumulatívne `human_cost` = súčet len cez verzie, kde je human_cost **non-None**; rovnako agent_cost; `eur_saved`/`m_cheaper` z týchto dvoch agregátov, a FE deklaruje pokrytie: **„kumulatívne za N z M verzií" (`covered_versions`/`total_versions`)**. Re-Gate / fast-fix re-build na rovnakom `version_id` re-emituje nové správy → korektne aditívne (viac reálnych tokenov, nie double-count).

### 1.4 Mapovanie role-of-origin → 6 comparison buckets (review #2, #8)

**Single source of truth = `ACTOR_VALUES` (`pipeline.py:61`)**, NIE `PipelineAgentRole` (ten je 5-agent-only, bez director/system — review #8 overené `user_agent_setting.py:21`). Derivovať:

```python
# metrics.py — derived from the canonical actor tuple, so a new actor can't fall out silently
from db.models.pipeline import ACTOR_VALUES   # ("coordinator","designer","customer","implementer","auditor","director")
COMPARISON_ROLES = tuple(r for r in ACTOR_VALUES if r != "director")   # 5 agentov
DIRECTOR_ROLE = "director"                                             # human overhead, agent strana
# "system" (PARTICIPANT_VALUES − ACTOR_VALUES) = engine-only; handling viď nižšie
```

**`system` token symetria (review #2) — explicitné rozhodnutie:** Po fix #1 sa väčšina engine-foldnutých tokenov vráti do reálnej roly cez `metrics_role`. Genuinely `system`-authored metered turny (napr. `_record_internal_turn_parse_failure`, smoke notifikácie nesúce timing) ostanú. **Rozhodnutie: `system` je vylúčený z OBOCH strán a označený „engine overhead (neporovnané)".** Headline `agent_cost_total` = **Σ per-role agent cost cez `COMPARISON_ROLES` + DIRECTOR_ROLE** (NIE version-grand-total). `system` cost sa zobrazí ako samostatný „Systém / engine" riadok info-only, nikdy nevstúpi do `m_cheaper`/`eur_saved`. Tým agent aj human total pokrývajú presne tú istú množinu rolí → symetria zachovaná, žiadna strana nenesie cost, ktorý druhá nemá. Per-verzia tabuľka footuje cez tento explicitný `system` riadok (review #14).

---

## 2. Computation — per-role agent vs human

Rozšíriť `metrics.py`. Žiadny nový live `claude` call, čistý read-time výpočet nad existujúcimi `PipelineMessage.payload`.

### 2.1 Agent cost — tokeny × API cena PER MODEL (review #5, #9)

`_api_cost` (`metrics.py:104-108`) zovšeobecniť na per-model s **explicitným, usporiadaným fallback reťazcom**:

```python
def _model_family(model_id: str | None) -> str:
    """Map a full model id (claude-opus-4-8 …) to a price family. Logged warning on no match,
    so a model roll that changes the family token surfaces as '_unknown', never silently mis-priced."""
    if not model_id:
        return "_unknown"
    mid = model_id.lower()
    for fam in ("opus", "sonnet", "haiku"):
        if fam in mid:
            return fam
    logger.warning("metrics: unrecognized model id %r → _unknown bucket", model_id)
    return "_unknown"

def _resolve_price(db, family: str, flat_in: float, flat_out: float) -> tuple[float, float]:
    """Ordered: per-family key → flat pair → (env baked into flat). _unknown uses flat directly."""
    if family != "_unknown":
        pin = _effective_price(db, f"api_price_input_per_mtok_{family}", 0.0)
        pout = _effective_price(db, f"api_price_output_per_mtok_{family}", 0.0)
        if pin > 0 and pout > 0:
            return pin, pout
    return flat_in, flat_out   # flat pair (system_settings → env fallback) covers _unknown + unkeyed families

def _api_cost_by_model(db, by_model, flat_in, flat_out) -> Optional[float]:
    """Σ_family (in×price_in + out×price_out)/1e6. None ONLY if a PRESENT model resolves to
    no price after the full fallback chain — a fully-priced set with _unknown mass costed at
    the flat pair is LEGITIMATE (the 'paid for compute, envelope didn't name the model' case)."""
    total = 0.0
    for model_key, mt in by_model.items():
        pin, pout = _resolve_price(db, _model_family(model_key), flat_in, flat_out)
        if pin <= 0 or pout <= 0:
            return None
        total += mt.input_tokens * pin + mt.output_tokens * pout
    return total / 1_000_000.0 if by_model else None
```

**Korekcia oproti draftu (review #5):** pôvodné „any unpriced model → whole None" KOLIDOVALO s „flat fallback pre `_unknown`". Vyriešené: `_unknown` (a každá nekľúčovaná rodina) sa **legitímne** ocení flat párom; `None` len keď ani flat pár nie je nastavený. Keďže `_DispatchMetrics.model` je `None` kedykoľvek `saw_usage=True` ale `usage.model` chýbal (`:117-119`), reálne behy BUDÚ mať `_unknown` masu — tá sa flat-cení, nenulifikuje celé číslo.

**Per-model vs flat — odporúčanie:** per-rodina kľúče (opus/sonnet/haiku) s flat fallbackom — modely sa reálne miešajú (Opus Koordinátor vs Sonnet monitoring per Sonnet-routing memory). **Model drift visibility (review #9):** FE povrchne ukáže self-check „X % tokenov bez rozpoznaného modelu" (podiel `_unknown` masy), aby koncentrácia `_unknown` na drahšej rodine bola viditeľná, nie tichá. Toto je per-model analóg honest-by-construction.

### 2.2 Human-time = tokeny × per-role kurz (tokens→minutes)

Nahrádza `_human_minutes` (Σ `Task.estimated_minutes`, `metrics.py:111-128`) — ten sa **DROPNE** (§6).

```python
def _human_minutes_for_role(t: UsageTotals, conv_rate: float) -> Optional[float]:
    """tokens → minutes via per-role conversion (minutes per 1M total tokens). None when rate unset (0)."""
    if conv_rate <= 0:
        return None
    return (t.input_tokens + t.output_tokens) / 1_000_000.0 * conv_rate
```

Jednotka = **minút na 1M tokenov** (per rola): čitateľné, ladí s API-cena jednotkou (per Mtok), Director ladí jedno číslo per rolu („nesedí → zmením kurz → prepočet" = jeden PATCH + reload, žiadny rebuild). `model` neovplyvňuje human-time (človek nemá „model"); kurz je čisto per-rola.

### 2.3 Human cost = human-time × per-role mzda

```python
def _human_cost(human_minutes: Optional[float], wage: float) -> Optional[float]:
    if human_minutes is None or wage <= 0:
        return None
    return human_minutes / 60.0 * wage
```

`developer_hourly_rate` (`system_setting.py:176-184`) sa **generalizuje** na mzdu Programátora/Implementera (locked: „reuse/generalize existing developer-rate"). Viď §3.3.

### 2.4 Idle split (wall-clock, NIE súčasť token→time) — real wall-clock (review #3)

- **(a) director-wait** = `_director_wait_seconds` (`metrics.py:69-77`) — UŽ tracked cez `PipelineState.total_director_wait_seconds` + live open wait (`:74-76`). Bez zmeny. Human-in-the-loop overhead na drive-to-zero.
- **(b) internal idle** = `total_time_seconds − active − director_wait`, kde `active = Σ timing.duration_seconds`.

**Fix #3 (overené `:80-91`):** `_total_time_seconds` pre **released** verziu (`:83`) = `(release_date − created_at.date()).days × 86400` = **celé dni**. Same-day build → `total_time == 0` → `internal_idle = max(0 − active − wait, 0) = 0` (tichom prehltne reálny idle) a nezreconciluje s FE wall-clock. **Fix: odvodiť released wall-clock z `min/max(PipelineMessage.created_at)`** (in-progress vetva to už robí `:84-88`); `release_date` len ako fallback keď nie sú žiadne správy. `internal_idle = None` keď message-derived span je 0/unknown, **nikdy 0**:

```python
def _total_time_seconds(db, version) -> Optional[float]:
    # message-derived span FIRST (granularity = real timestamps, not integer days)
    first, last = db.execute(select(func.min(PipelineMessage.created_at),
                                    func.max(PipelineMessage.created_at))
                             .where(PipelineMessage.version_id == version.id)).one()
    if first is not None and last is not None and last > first:
        return (last - first).total_seconds()
    # fallback only when no messages span exists
    if version.release_date is not None:
        return float(max((version.release_date - version.created_at.date()).days, 0) * 86400) or None
    return None

def _internal_idle_seconds(total, active, director_wait) -> Optional[float]:
    if total is None or total <= 0:
        return None              # never fabricate 0
    return max(total - active - director_wait, 0.0)
```

### 2.5 Director intervencie — count + Director-time cost; symetrická human strana (review #10)

Director správy = `author == "director"` (`pipeline.py:61`, vzor `orchestrator.py:773`):

```python
def _director_interventions(db, version_id) -> int:
    return db.execute(select(func.count()).select_from(PipelineMessage)
                      .where(PipelineMessage.version_id == version_id,
                             PipelineMessage.author == "director")).scalar() or 0
```

**Agent-side Director cost** = `director_wait_seconds × metrics_hourly_wage_director` — **empirický** (meraný čas, kedy agent system čakal na Directora).

**Fix #10 — symetria oboch Director cien (najhratelnejšie číslo):** Pôvodný návrh (`director_overhead_pct × Σ human role-time`) miešal jedno **merané** (agent) a jedno **vymyslené** (human) číslo → skeptik to zlomí prvé. **Oprava: obe strany odvodzujú Director čas rovnakým modelom — `director_minutes_per_human_role_hour` (jeden tunable kurz):**

```python
# Human-side Director time = same intervention-rate model applied to the human process:
#   human_director_minutes = Σ_role human_minutes / 60 * director_minutes_per_human_role_hour
# Agent-side Director time  = MEASURED director_wait (empirical).
# Both costed at metrics_hourly_wage_director → like-for-like. Point ("agent Director is fractional")
# now compares measured-small vs rate-derived-from-the-same-rate, not measured vs arbitrary multiplier.
```

`director_minutes_per_human_role_hour` (default 0 → human-side Director = None). Tým je tvrdenie „agent-side Director je frakčný" obhájiteľné: obe strany cez ten istý kurz a tú istú mzdu.

### 2.6 Speed ratio + cost ratio + EUR saved

```python
# Speed: token-derived human-time vs agent ACTIVE time (Σ duration_seconds, NIE wall-clock, NIE director-wait)
x_faster = (human_minutes_total / agent_active_minutes) if (both > 0) else None
# Cost: M× cheaper + EUR saved (per-version aj cumulative)
m_cheaper = (human_cost_total / agent_cost_total) if (both not None and agent_cost_total > 0) else None
eur_saved = (human_cost_total - agent_cost_total) if (both not None) else None
```

`y_cheaper_pct` (`metrics.py:203-207,223`) nahradené `m_cheaper` + `eur_saved`. Všetko `None`-guarded.

### 2.7 Per-row honest coherence (review #4)

Riadok môže mať reálny `human_cost` (rate+wage set) vedľa `agent_cost = —` (jeden model neocenený) → číta sa ako „rozbité", nie „honest". **Fix:** `m_cheaper`, `eur_saved` aj „ušetrené" bunka riadku renderujú `—` kedykoľvek je **ktorákoľvek** strana `None`; navyše per-row config badge vysvetľujúci `None` agent-cost (napr. „AI cena chýba: model `X` nenacenený") — `None` je vysvetlený, nie záhadný. Spec gap (labeling+guard), nie len FE polish.

### 2.8 Koordinátor kurz kalibrácia — explicitný fragility lock (review #7)

`verify_done` beží Koordinátor na **každom** gate_report a **každej** worker otázke (`:1948`, `:2099`) + release — reálne Opus turny, ale **deterministická orchestračná réžia**, nie „koordinačné myslenie per token". §5 seed kurz (300 min/1M) aplikovaný na túto nafúknutú masu = veľký fiktívny human-PM čas.

**Rozhodnutie (b) explicitne zamknuté:** Koordinátor kurz je kalibrovaný *proti nafúknutej engine-relay mase* (nižší kurz ju absorbuje). Spec **explicitne uvádza**, že kalibrácia je krehká voči engine-chattiness: budúci Coordinator refactor pridávajúci relay turny ticho nafúkne „ušetrené PM hodiny". Mitigácia bez nového stavu: payload flagy `is_synthesis`/`is_director_brief` UŽ existujú (`:1673`, `:1552`, `:2148`) → FE povrchne ukáže podiel „judgment vs relay" Koordinátor tokenov (info-only), takže drift kalibrácie je viditeľný. Plný judgment/relay split kurzu = deferred decision (§Otvorené), nie blocker.

---

## 3. Settings — per-role kurz, per-role mzda, Director mzda + kurz

Settings layer je plne data-driven: jediná backend zmena = registrácia nových `_Default` v `DEFAULT_SETTINGS` (`system_setting.py:50`). Žiadna migrácia (defaults rezolvujú bez seed row), žiadna schema zmena, žiadna route zmena, FE renderuje automaticky.

### 3.1 Per-role kurz (tokens→minutes), 5 rolí

```python
"metrics_minutes_per_mtok_coordinator": _Default(value="0.0", value_type="float",
    description="Human-equivalent minutes per 1,000,000 total tokens for the Coordinator role. "
                "0 = unset → that role's human-time/cost null (never fabricated)."),
"metrics_minutes_per_mtok_designer":    _Default(value="0.0", value_type="float", description="… Designer …"),
"metrics_minutes_per_mtok_customer":    _Default(value="0.0", value_type="float", description="… Customer …"),
"metrics_minutes_per_mtok_implementer": _Default(value="0.0", value_type="float", description="… Implementer …"),
"metrics_minutes_per_mtok_auditor":     _Default(value="0.0", value_type="float", description="… Auditor …"),
```

### 3.2 Per-role mzda, 5 rolí + Director

```python
"metrics_hourly_wage_coordinator": _Default(value="0.0", value_type="float", description="Hourly wage, Coordinator-equivalent human role. 0 = unset → null."),
"metrics_hourly_wage_designer":    _Default(value="0.0", value_type="float", description="… Designer-equivalent …"),
"metrics_hourly_wage_customer":     _Default(value="0.0", value_type="float", description="… Customer-equivalent …"),
"metrics_hourly_wage_implementer": _Default(value="0.0", value_type="float",
    description="Hourly wage, Implementer/Programmer-equivalent. SUPERSEDES developer_hourly_rate "
                "(read as fallback ONLY when this key has no row; explicit 0 = honored as unset)."),
"metrics_hourly_wage_auditor":     _Default(value="0.0", value_type="float", description="… Auditor-equivalent …"),
"metrics_hourly_wage_director":    _Default(value="0.0", value_type="float", description="Hourly wage of the human Director — costs BOTH the measured agent-side director-wait AND the human-side director time (§2.5)."),
"metrics_director_minutes_per_human_role_hour": _Default(value="0.0", value_type="float",
    description="Human-side Director minutes per hour of human role-work (the human team also has a director). "
                "Same intervention-rate model as the measured agent-side director-wait. 0 = unset → human director null."),
```

### 3.3 Generalizácia `developer_hourly_rate` (review #13)

Neodstrániť (back-compat). Rozlíšiť „unset (žiadny row → použi fallback)" od „explicitne 0 (honor ako None)" — `get_float` vracia 0.0 aj pre unset aj pre explicit 0, takže fallback cez `or` by prepísal úmyselnú 0. Riešenie: čítať cez existenciu row, nie cez truthiness:

```python
# explicit-0 vs unset: row exists → honor its value (incl. 0); no row → fall back to developer_hourly_rate
impl_wage = system_setting.get_float_or_none(db, "metrics_hourly_wage_implementer")   # None when no row
if impl_wage is None:
    impl_wage = _effective_price(db, "developer_hourly_rate", settings.developer_hourly_rate)
```

(`get_float_or_none` = tenký variant existujúceho `get_float` — vracia `None` keď row chýba namiesto 0.0. Ak ho repo nemá, je to malý helper v `system_setting.py` vedľa `get_float :251`.)

### 3.4 Per-model ceny (odporúčané)

```python
# pre rodiny z AgentModel (user_agent_setting.py:23 → opus / sonnet / haiku)
"api_price_input_per_mtok_opus":   _Default(value="0.0", value_type="float", description="IN price/Mtok for Opus. Falls back to api_price_input_per_mtok (flat)."),
"api_price_output_per_mtok_opus":  _Default(value="0.0", value_type="float", description="OUT price/Mtok for Opus."),
"api_price_input_per_mtok_sonnet": _Default(value="0.0", value_type="float", description="IN price/Mtok for Sonnet."),
"api_price_output_per_mtok_sonnet":_Default(value="0.0", value_type="float", description="OUT price/Mtok for Sonnet."),
"api_price_input_per_mtok_haiku":  _Default(value="0.0", value_type="float", description="IN price/Mtok for Haiku."),
"api_price_output_per_mtok_haiku": _Default(value="0.0", value_type="float", description="OUT price/Mtok for Haiku."),
```

Model-key derivácia: `payload.usage.model` je full ID (`claude-opus-4-8` …); `_model_family` substring match (§2.1), logged warning na `_unknown`. Flat pár `api_price_*_per_mtok` (existujúci, `metrics.py:133-134`) ostáva ako fallback pre `_unknown` + nekľúčované rodiny.

### 3.5 Persistence / route / authz — BEZ ZMENY

- Žiadna migrácia: defaults rezolvujú bez DB row; row vznikne na prvom edite.
- `upsert` validuje proti `DEFAULT_SETTINGS` + `value_type` (`system_setting.py:355-373,394-396`) — nové kľúče automaticky validné.
- `PATCH` je `ri`-only (`system_settings.py:55`) — Director (`ri`) autorizovaný. Žiadna nová authz.
- Cache invalidácia na upsert (`system_setting.py:419`) — edit viditeľný hneď.

### 3.6 FE Settings — nová kategória (review #15)

`SystemSettingsPanel` auto-renderuje každý kľúč; `value_type="float"` → number input `step="any"`. Pridať kategóriu do `SETTINGS_CATEGORIES` (`SettingsPage.tsx:75`):

```typescript
{
  id: "metrics",
  label: "Metriky / ROI (kurzy a mzdy)",
  description: "Per-rola kurz tokeny→minúty (human-time), per-rola hodinová mzda, Director mzda + kurz, API ceny per model. 0 = nenastavené → ROI sa nezobrazí vymyslené.",
  prefixes: ["metrics_", "api_price_"],
  keys: ["developer_hourly_rate"],     // EXACT membership (review #15: prefix would also catch future api_price_* non-metrics keys)
},
```

(Ak `SETTINGS_CATEGORIES` nepodporuje exact-key membership popri prefixoch, pridať jednoduchú `keys?: string[]` vetvu vo filtri — drobná FE zmena, prefer exact match pre `developer_hourly_rate`.)

---

## 4. FE MetricsPage layout

### 4.1 Nový data contract — `frontend/src/types/metrics.ts`

```typescript
export interface ModelTokens { input_tokens: number; output_tokens: number; }

export interface RoleMetric {
  role: string;                       // one of COMPARISON_ROLES
  // AGENT
  active_seconds: number;             // Σ timing.duration_seconds
  internal_idle_seconds: number | null;
  input_tokens: number;
  output_tokens: number;
  parse_attempts: number;             // rework evidence (review #12)
  agent_cost: number | null;          // tokens × per-model API price; null if any present model unpriced after fallback
  agent_value_in: number | null;
  agent_value_out: number | null;
  by_model: Record<string, ModelTokens>;
  unpriced_model_keys: string[];      // for the per-row "AI cena chýba: model X" badge (review #4)
  // HUMAN
  human_minutes: number | null;       // tokens × per-role rate
  human_cost: number | null;          // human_minutes × per-role wage
  // ratios (— when EITHER side null — review #4)
  x_faster: number | null;
  m_cheaper: number | null;
  eur_saved: number | null;
}

export interface SystemOverheadRow {  // review #2, #14 — un-compared engine tokens, foots the table
  input_tokens: number; output_tokens: number;
  active_seconds: number; agent_cost: number | null;
}

export interface DirectorMetric {
  interventions: number;
  agent_wait_seconds: number;         // measured (idle-a)
  agent_director_cost: number | null; // wait × director wage  (empirical, agent side)
  human_director_minutes: number | null;
  human_director_cost: number | null; // human-side, same rate model (review #10)
}

export interface RoiHeadline {
  agent_active_minutes: number;
  human_minutes_total: number | null;
  agent_cost_total: number | null;    // Σ role agent cost + director (NOT incl. system — review #2)
  human_cost_total: number | null;
  x_faster: number | null;            // human-time vs agent ACTIVE time
  m_cheaper: number | null;
  eur_saved: number | null;
  unknown_model_token_pct: number;    // model-drift visibility (review #9)
  flat_subscription: boolean;         // ROI angle label (§2.7 orig / locked)
  marginal_cost_eur: 0;
  configured: boolean;                // pricing AND rates AND wages (review #11)
  pricing_configured: boolean;
  rates_configured: boolean;
  wages_configured: boolean;
  covered_versions: number;           // cumulative coverage (review #6)
  total_versions: number;
}

export interface VersionMetrics {
  version_id: string; version_number: string; status: string;
  usage: UsageTotals;
  by_role: RoleMetric[];              // 5 agent roles
  system_overhead: SystemOverheadRow;
  director: DirectorMetric;
  director_wait_seconds: number;
  internal_idle_seconds: number | null;
  total_time_seconds: number | null;
  roi: RoiHeadline;
}

export interface ProjectMetrics {
  project_id: string; slug: string;
  usage: UsageTotals;
  by_role: RoleMetric[];              // cumulative per role
  system_overhead: SystemOverheadRow;
  director: DirectorMetric;
  by_version: VersionMetrics[];
  roi: RoiHeadline;                   // cumulative
}
```

`ScopeUsage`, `by_epic/feat/task`, old `Roi`, `estimates_configured` → odstránené (§6).

### 4.2 Schema mirror — `backend/schemas/metrics.py`

`RoleUsageRead` → `RoleMetricRead` (poľa z §4.1). Nové `SystemOverheadRead`, `DirectorMetricRead`, `RoiHeadlineRead`. `ScopeUsageRead` (`:25-31`) + `VersionMetricsRead.by_epic/feat/task` (`:46-48`) + `RoiRead` (`:58-70`) odstránené. Mirror 1:1 s TS.

### 4.3 Layout (Slovak UI, honest dash)

**Hlavička (headline ROI) — toggle Verzia / Kumulatívne:**
- `N× rýchlejšie` — `roi.x_faster`, hint „ľudský čas (z tokenov) vs aktívny AI čas".
- `M× lacnejšie` — `roi.m_cheaper`, hint „ľudská cena vs API cena (trhová hodnota compute)".
- `Ušetrené € (tento build)` — `roi.eur_saved` per verzia.
- `Ušetrené € (kumulatívne)` — project `roi.eur_saved` + „za N z M verzií" (`covered_versions`/`total_versions`).
- Badge: „Platíme flat Claude MAX → marginálny náklad ~0; vyššie je trhová hodnota spotrebovaného compute."
- Badge (keď `unknown_model_token_pct > 0`): „X % tokenov bez rozpoznaného modelu — cena flat."

**Per-rola tabuľka** (5 riadkov + Director overhead riadok + Systém/engine riadok pre footing):

| Rola | AGENT: aktívny čas | idle | tokeny IN/OUT | rework (pokusy) | hodnota IN/OUT (€) | HUMAN: čas | hodnota (€) | N× | M× | ušetrené € |
|---|---|---|---|---|---|---|---|---|---|---|
| Koordinátor | … | … | … | `parse_attempts` | … | … | … | … | … | … |
| Návrhár | … | … | … | … | … | … | … | … | … | … |
| Zákazník | … | … | … | … | … | … | … | … | … | … |
| Audítor | … | … | … | … | … | … | … | … | … | … |
| Programátor | … | … | … | … | … | … | … | … | … | … |
| **Director (overhead)** | čakanie `director.agent_wait_seconds` · intervencie `director.interventions` | — | — | — | agent `director.agent_director_cost` / human `director.human_director_cost` | — | — | — | — | — |
| *Systém / engine (neporovnané)* | `system_overhead.active_seconds` | — | `system_overhead` IN/OUT | — | `system_overhead.agent_cost` | — | — | — | — | — |

Labels z `ROLE_LABELS` (`components/cockpit/labels.ts:40-48`, single source — overené: obsahuje `director` aj `system`), nikdy raw author string. Director riadok = intervention count + obojstranný Director-time cost (agent meraný / human odvodený rovnakým kurzom), hint „agent-side frakčné voči ľudskému Directorovi tímu". Per-row badge na `agent_cost = —`: „AI cena chýba: model `{unpriced_model_keys}` nenacenený" (review #4). Systém riadok zabezpečuje, že per-role súčet + Director + Systém = version grand-total (review #14).

**Idle panel** (oddelený, NIKDY miešaný do AI času — zachovať `MetricsPage.tsx:219-224`):
- `Čakanie na Directora (idle-a)` = `director_wait_seconds`, „human-in-the-loop overhead, cieľ → 0".
- `Interný idle (idle-b)` = `internal_idle_seconds` (real wall-clock, `—` keď span neznámy/0 — review #3).

**Version selector + Kumulatívne** (reuse `MetricsPage.tsx:263-273`): toggle „Verzia ▾ / Kumulatívne" prepína celý view.

**Per-rola chart** (nahrádza scope EPIC/FEAT/TASK chart `MetricsPage.tsx:274-308`): grouped bar **agent active min vs human min** + druhý **agent € vs human €** per rola.

**Honest dash:** `fmtCost(null) → "—"` (`MetricsPage.tsx:46-48`) zachované; každá `*_cost`, `*_minutes`, `x_faster`, `m_cheaper`, `eur_saved` rendruje „—" keď `null` (a per §2.7 keď **ktorákoľvek** strana null). Banner (`MetricsPage.tsx:205-210`) rozšíriť: „Kurzy/mzdy/ceny nenastavené → ľudská/AI strana sa nezobrazí; doplň v Nastavenia → Metriky / ROI." s per-dimenziou (`pricing/rates/wages_configured`).

### 4.4 API — `frontend/src/services/api/metrics.ts`

Bez zmeny endpointu (`GET /projects/${slug}/metrics`, `metrics.ts:5-7`) — len reshaped payload + typy. Route handler nezmenený, volá `compute_project_metrics`.

---

## 5. Iniciálne per-role kurzy (Dedo seed) — návrh + zdôvodnenie

Jednotka = **minút ľudskej práce na 1M total tokenov (IN+OUT)** per rola. Kalibrácia: typický agent turn nesie rádovo desiatky tisíc IN tokenov (context-heavy reading) + jednotky tisíc OUT. 1M tokenov ≈ „veľký kus práce role". Kurz odráža **koľko by trval ekvivalentný ľudský výkon** (čítanie + premýšľanie + produkcia + chyby + rework), keďže báza zámerne obsahuje retries (= ľudské chyby).

| Rola | Kurz (min / 1M tok) | Zdôvodnenie |
|---|---|---|
| **Návrhár (designer)** | **520** | Najťažšia kognitívna práca — kompletná špecifikácia pred kódom (waterfall §2). 1M tokenov spec-práce ≈ ~8.5 h seniora architekta. Najvyšší kurz. |
| **Audítor** | **460** | Systematic verification, spec↔impl, adversarial čítanie. Pomalá dôsledná práca; o niečo nižšia než tvorba. |
| **Zákazník (customer)** | **400** | Gate E — systematické hľadanie dier v zadaní. Doménová analýza, užší scope než full design. |
| **Koordinátor** | **300** | Orchestrácia, triage, relay. **Kalibrované proti nafúknutej engine-relay mase** (verify_done per gate/otázka) → nižší kurz ju absorbuje. **Krehké voči engine-chattiness** (review #7) — kurz je bezvýznamný bez tejto volume-assumption; FE ukazuje judgment/relay podiel ako kontrolu driftu. |
| **Programátor (implementer)** | **240** | Najnižší — coding je per-token najrýchlejšia ľudská aktivita (skúsený dev píše/číta kód rýchlejšie než spec prózu); väčšina OUT tokenov je kód. 1M tok ≈ ~4 h seniora. |

Director (intervencie) sa nekonvertuje per-token kurzom — meria sa wall-clock wait + count (agent), resp. `director_minutes_per_human_role_hour` (human, §2.5).

Mzdy (Dedo seed, currency-agnostic, Director doladí): designer/auditor/customer ≈ senior architekt, implementer ≈ `developer_hourly_rate` (generalizovaný), coordinator ≈ tech-lead, director = Director sadzba. Konkrétne €-čísla na Directora (mzdy sú trhovo-špecifické); kurzy vyššie sú relatívne kalibrované a **plne tunable** (PATCH + reload, žiadny rebuild).

Všetky seed hodnoty cez Settings UI (PATCH), NIE do `DEFAULT_SETTINGS` (tie ostávajú `"0.0"` kvôli honest-by-construction — bez seedu = „nenastavené", nie fake).

---

## 6. Migrácia z EPIC-FEAT-TASK

### Drop
- `pipeline_metrics.py`: `by_task/by_feat/by_epic` v `PipelineUsageAggregate` (`:50-52`) + ich roll-up v `aggregate_pipeline_usage` (`:107-120`). `aggregate_pipeline_usage` ostáva len pre **version-grand-total**.
- `metrics.py`: `_usage_by_role` (`:51-66`, povýšené → `aggregate_usage_by_role` v data layeri), `_scope_rows` (`:94-101`), `epic_meta/feat_meta/task_meta` (`:150-167`), `by_epic/feat/task` rows (`:186-188`), `_human_minutes` (`:111-128`), `_api_cost` flat-only (`:104-108` → `_api_cost_by_model`), `RoiRead` produkcia + `y_cheaper_pct` (`:203-225`), `estimates_configured` (`:198,227`).
- `schemas/metrics.py`: `ScopeUsageRead` (`:25-31`), `VersionMetricsRead.by_epic/feat/task` (`:46-48`), `RoiRead` (`:58-70`).
- `types/metrics.ts`: `ScopeUsage` (`:11-16`), `by_epic/feat/task` (`:28-30`), old `Roi` (`:37-45`).
- `MetricsPage.tsx`: `ScopeLevel`/`SCOPE_LABEL` (`:20-26`), `scopeChartData` (`:124-132`), EPIC/FEAT/TASK toggle + scope chart (`:274-308`).
- `developer_hourly_rate` — **NIE drop**, ostáva ako fallback pre implementer wage (§3.3).

### Keep (load-bearing spine, reuse verbatim)
- **Capture layer** (`orchestrator.py:85-181`) — single reproducible base; emituje `None` nie fake nuly. **Jediná zmena = `metrics_role` na 2 leak miestach + 1 seed helper (§1.1), nič iné.**
- `_effective_price` + `system_setting.get_float` + `DEFAULT_SETTINGS` float pattern (`metrics.py:46-48`, `system_setting.py:251-266,355-420`) — reuse pre nové kľúče.
- `_director_wait_seconds` (`metrics.py:69-77`), `PipelineState.total_director_wait_seconds` + listener (`pipeline.py:120-128,245-270`) — idle-a bez zmeny.
- `UsageTotals` (`pipeline_metrics.py:29-42`) — rozšírený o `by_model`/`parse_attempts`/`merge`, nie nahradený.
- Všetky `None`-honesty guards — preserved + rozšírené.
- FE `fmtCost/fmtInt/fmtDuration`, `Card`, version selector, banner, theme chart chrome — reuse.
- `ROLE_LABELS` (`labels.ts:40-48`) — single label source incl. director/system.

### Bez migrácie DB
Žiadna schema/Alembic migrácia: per-message capture nezmenený (`metrics_role` je voliteľný payload kľúč v existujúcom JSON `payload`, žiadny stĺpec), `system_settings` defaults rezolvujú bez seed row, EPIC/FEAT/TASK ORM modely (`Epic/Feat/Task`) ostávajú (core plánu, len ich metrics roll-up sa prestane volať). Data backfill netreba — agregácia je read-time nad existujúcimi `PipelineMessage.payload`. **Pozn. (review #1):** historické verzie postavené pred touto zmenou nemajú `metrics_role` → ich engine-foldnuté tokeny ostanú na pôvodnom `author` (coordinator/system) = legacy mis-attribution len pre staré buildy; nové buildy správne. Akceptovateľné (nefabrikujeme retro-atribúciu); FE to neoznačuje.

### Poradie implementácie (pre Implementera, ak Director schváli)
1. `system_setting.py` — nové `_Default` kľúče (§3.1-3.4) + `get_float_or_none` helper (§3.3).
2. `orchestrator.py` — `metrics_role` na 2 leak miestach (`:1530-1536`, `:5117-5134`) + seed helper (§1.1).
3. `pipeline_metrics.py` — `UsageTotals.by_model/parse_attempts/merge` + `ModelTokens` + `aggregate_usage_by_role`.
4. `metrics.py` — `_api_cost_by_model`/`_model_family`/`_resolve_price`, per-role human-time/cost, idle split (real wall-clock), director count + symetrický cost, ratios, cumulative-by-role (None-safe coverage); drop scope.
5. `schemas/metrics.py` — reshape (RoleMetricRead/SystemOverheadRead/DirectorMetricRead/RoiHeadlineRead; drop scope).
6. `types/metrics.ts` + `MetricsPage.tsx` + `SettingsPage.tsx` kategória.
7. FE = prod build (nginx static) — `docker compose build frontend && up -d` (per memory).

### Flag na Designer (review #16)
`_human_minutes` drop odstraňuje jediného metrics-konzumenta `Task.estimated_minutes`/`Feat.estimated_minutes`. Stĺpce ostanú v ORM (neorphaned z DB pohľadu), ale **task-plan generačné prompty, ktoré inštruujú agenta produkovať `estimated_minutes`, sa stávajú dead instructions** — flagnúť Designerovi na rozhodnutie (ponechať estimate-produkciu pre iný účel, alebo vyčistiť prompt), nesilent-leave.

---

## Súhrn rozhodnutí (decisive)

- **Báza + atribúcia:** rola-pôvodu (`metrics_role`), NIE autor-záznamu — opravuje 2 leak miesta (worker→coordinator seed, fail-Implementer→system), inak per-role split nesprávny (review #1).
- **`system` tokeny:** vylúčené z OBOCH strán, samostatný „engine overhead" riadok (footuje tabuľku); headline totals = Σ rolí + Director, nie grand-total → symetria (review #2, #14).
- **Released wall-clock:** z `min/max(created_at)`, nie integer-day `release_date`; internal-idle `None` keď span 0/unknown (review #3).
- **Honest coherence:** per-row `m_cheaper`/`eur_saved` = `—` keď ktorákoľvek strana `None`, + badge vysvetľujúci chýbajúcu AI cenu (review #4).
- **Per-model cena:** per-rodina kľúče + flat fallback pre `_unknown` (legitímne ocenený, nenulifikuje); `None` len keď ani flat nie je (review #5); model-drift % viditeľné (review #9).
- **Kumulatívne ROI:** None-safe, počítané z agregátov non-None verzií, deklaruje „za N z M verzií" (review #6).
- **Koordinátor kurz:** zamknuté ako kalibrované proti engine-relay mase + explicitný fragility note + FE judgment/relay podiel (review #7).
- **Role source:** `ACTOR_VALUES` (`pipeline.py:61`), NIE `PipelineAgentRole` (review #8).
- **Director symetria:** obe strany cez rovnaký kurz (`director_minutes_per_human_role_hour`) + rovnakú mzdu — žiadne meraný-vs-vymyslený mix (review #10).
- **Configured:** headline `configured = pricing AND rates AND wages`; per-dimenzia bools zachované pre banner (review #11).
- **Retry tokens:** best-effort (capture nemôže fabrikovať); wall-clock garantovane úplný; `parse_attempts` ako rework evidence na riadku (review #12).
- **`developer_hourly_rate`:** fallback len keď nový kľúč nemá row; explicit 0 honored (review #13).
- **Settings kategória:** `metrics_`/`api_price_` prefix + exact `developer_hourly_rate` (review #15).
- **Žiadna DB migrácia, žiadna route/authz zmena** — settings data-driven, `metrics_role` je payload kľúč, FE renderuje nové kľúče automaticky.

Súbory na zmenu (všetky absolútne):
- `/opt/projects/nex-studio/backend/services/orchestrator.py` (`metrics_role` na `:1530-1536`, `:5117-5134` + seed helper — pôvodný draft tvrdil „bez zmeny", review #1 to opravil)
- `/opt/projects/nex-studio/backend/services/pipeline_metrics.py`
- `/opt/projects/nex-studio/backend/services/metrics.py`
- `/opt/projects/nex-studio/backend/schemas/metrics.py`
- `/opt/projects/nex-studio/backend/services/system_setting.py`
- `/opt/projects/nex-studio/backend/config/settings.py` (per-model env fallbacky, voliteľné)
- `/opt/projects/nex-studio/frontend/src/types/metrics.ts`
- `/opt/projects/nex-studio/frontend/src/pages/MetricsPage.tsx`
- `/opt/projects/nex-studio/frontend/src/pages/SettingsPage.tsx` (kategória „metrics")
- `/opt/projects/nex-studio/frontend/src/services/api/metrics.ts` (bez zmeny endpointu; len typy)

Bez zmeny (reuse verbatim): `/opt/projects/nex-studio/backend/db/models/pipeline.py` (`ACTOR_VALUES :61`, director-wait listener `:245-270`), `/opt/projects/nex-studio/backend/api/routes/system_settings.py` (authz), `/opt/projects/nex-studio/frontend/src/components/cockpit/labels.ts` (`ROLE_LABELS :40-48`).

---

## Otvorené rozhodnutia pre Directora

Dve genuine voľby zostávajú (všetko ostatné je deterministicky odvodené alebo sú to mzdy/kurzy, ktoré ladíš v Nastaveniach):

1. **Koordinátor judgment-vs-relay split kurzu (review #7).** Súčasný návrh: jeden Koordinátor kurz kalibrovaný proti celej (nafúknutej) engine-relay mase — jednoduché, ale krehké voči budúcim Coordinator refaktorom (pridanie relay turnov ticho nafúkne „ušetrené PM hodiny"). Alternatíva: rozdeliť Koordinátor tokeny na „judgment" (verify_done s `is_synthesis`/`is_director_brief`) vs „mechanical relay" a aplikovať kurz len na judgment — kredibilnejšie, ale jeden kurz navyše + zložitejší výpočet. Odporúčam **single kurz teraz** (FE ukazuje judgment/relay podiel ako early-warning), split až keď engine-chattiness reálne narastie. Tvoja voľba.

2. **Human-side Director model (review #10).** Návrh: `director_minutes_per_human_role_hour` (human Director čas odvodený z human role-času, rovnaká mzda ako agent-side). Alternatíva: human-side Director úplne vynechať z cost a ponechať len agent-side meraný residual + kvalitatívne tvrdenie „frakčný". Odporúčam **symetrický kurz** (obhájiteľné voči skeptikovi: obe strany rovnakým modelom). Ak chceš čisto empirické porovnanie bez akéhokoľvek odhadu, zvoľ alternatívu — agent-side Director ostane meraný, human-side sa nezobrazí (None). Tvoja voľba.

Mzdy (6×) a kurzy (5×) sú čisto tvoje tuning hodnoty v Nastavenia → Metriky / ROI — Dedo seed je v §5, ty ich doladíš („nesedí → zmením kurz → prepočet").
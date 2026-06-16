# NEX Studio v0.7.0 — R3: Native Structured Output

> Design of record. Grounded by `r3-grounding` (parse-path anchors + a feasibility probe via claude-code-guide +
> the degradation-seam map). Class 2 (brittle parsing). Replace the hand-parsed `<<<PIPELINE_STATUS>>>` text
> fence with **grammar-constrained, schema-validated** agent output — the deepest robustness fix.

## 1. Goal
Every cockpit agent turn ends with a machine status block today wrapped in a `<<<PIPELINE_STATUS>>> … <<<END>>>`
text fence (`pipeline_status.py:35-38`), parsed by regex then JSON-decoded then Pydantic-validated. The fence is
**brittle**: any model drift (a stray prose line inside the fence, an unescaped quote, a forgotten END marker)
→ `ParseFailure` → a wasted retry or a `blocked` escalation. R3 makes the transport **enforced by the runtime**:
the agent is invoked with a JSON Schema and the model is grammar-constrained to emit a conforming object, so a
malformed block is impossible at the source — without lowering any gate or losing the existing retry/escalation.

**Feasibility (confirmed):** the deployed `claude` CLI is **2.1.178** in the cockpit container — it supports
`--json-schema <schema>` (verified in `--help`: "JSON Schema for structured output"), print-mode only (we use
`-p`). The validated object lands in the JSON envelope's `structured_output` field. This is the same forced-
structured-output facility the Claude Code Workflow tool uses; the cockpit just hasn't adopted it.

## 2. Director-approved design decisions
- **D1 — Mechanism: `--json-schema`.** Invoke the agent with the **PipelineStatusBlock JSON Schema** (derived
  from the existing Pydantic model — single source, not hand-written) passed as `--json-schema`; read the result
  from the envelope's `structured_output`. The **transport** changes (fence → `structured_output`); the
  **content contract** (the `PipelineStatusBlock` / `CoordinatorDirective` Pydantic models + every field
  validator + enum) is **UNCHANGED**.
- **D2 — Defense in depth (fence fallback STAYS).** `structured_output` is PRIMARY; if it's absent or fails
  Pydantic validation, **fall back to the existing fence parse of the result text**. So R3 is **non-breaking and
  rollout-safe** — a model/CLI that doesn't produce `structured_output` still parses exactly as today, and the
  parse-retry + escalation still fire. The fence parser is NOT removed.
- **D3 — Every degradation seam preserved.** The parse-retry loop (`_PARSE_RETRIES`), the cross-attempt metrics
  accumulation, the `ParseFailure` contract (`reason`/`usage`/`timing`/**`lost_work`** for R1-c), the
  `blocked`-escalation, and the Coordinator engine-failure relay all stay byte-for-byte. R3 only changes the
  transport + adds the primary structured path; it never changes WHEN a turn fails or HOW a failure escalates.
- **D4 — The 3-tuple change is audited (full blast radius).** `invoke_claude`/`_invoke_once`/`_invoke_streaming`
  return `(text, usage, structured_output)`. The change touches, in order: (a) the intermediate unpack helper
  **`_split_claude_result`** (`orchestrator.py:111`, used at `:978` — `text, usage = _split_claude_result(...)`)
  must thread the 3rd element; (b) `orchestrator.invoke_agent` (uses `structured_output`); (c) **`dialogue.py:215`**
  (`text, _usage = await invoke_claude(...)` → must unpack the 3-tuple; Gate E passes NO schema → `structured_output`
  is `None`, ignored); (d) **ALL test mocks of `invoke_claude` — 5 test files** currently return a bare string /
  2-tuple and WILL break on a 3-tuple unpack: updating every mock is a **mandatory CR precondition**, not an
  afterthought. A full `pytest` is the gate (the self-verify already runs it).
- **D5 — Charter/brief alignment is Dedo's (not the Implementer's).** The §7.2 charter contract + the
  `_directive_for` briefs say "Ukonči `<<<PIPELINE_STATUS>>>` blokom" — with `--json-schema` FORCING the shape,
  that instruction becomes defense-in-depth (it still tells the agent WHAT goes in each field). Charters live at
  `.claude/agents/**` and are **Dedo-maintained** (F-007 §12); the Implementer touches only the in-repo brief
  text + the spec, NOT the charters.
- **D6 — Scope: the cockpit's OWN dispatch path** (`claude_agent.py` via `invoke_agent`). `dedo-dispatch-implementer`
  (Dedo's external meta-tool) is OUT of scope (consistent with R1) — it may adopt `--json-schema` later.

## 3. Mechanism (grounded)
- **`claude_agent.py` (`:138-257`, `:260-305`)** — add `json_schema: Optional[dict] = None` to `invoke_claude`,
  `_invoke_once`, `_invoke_streaming`. When provided, append `["--json-schema", json.dumps(json_schema)]` to the
  args **before the positional prompt** (alongside the existing `--model`/`--effort`/session flags). Extract
  `structured_output` from the JSON envelope (non-streaming `:249-257`) and the stream-json result
  event (`:285-287`). Return `(text, usage, structured_output)` — `structured_output` is `None` when no schema is
  passed (Gate E) or absent.
- **`pipeline_status.py`** — export the schema once: `PIPELINE_STATUS_JSON_SCHEMA = PipelineStatusBlock.model_json_schema()`
  (single source — the model IS the schema). Add `parse_structured_output(obj: dict) -> ParseResult` that validates
  the dict **directly** through the SAME `PipelineStatusBlock` (reusing every validator), returning `ParseFailure`
  on a schema violation exactly like the fence path. **Keep** `parse_status_block` (fence) as the fallback.
- **`orchestrator.invoke_agent` (`:910-1087`, parse site `:1021`)** — pass `PIPELINE_STATUS_JSON_SCHEMA` to
  `invoke_claude`; if `structured_output is not None` → `parse_structured_output(structured_output)`; else (or on
  its `ParseFailure`) → `parse_status_block(text)` (the existing fence path). Everything downstream
  (`ParseFailure` wrapping, metrics, message recording, the `lost_work`/R1-c guard at `:1986`) is unchanged.
- **Parse-retry re-prompt (`:1142-1148`)** — make the failure text transport-agnostic (drop the fence-/escaped-
  quote-specific wording): "Tvoj štruktúrovaný výstup sa nepodarilo spracovať: {reason}. Pošli LEN platný objekt
  podľa schémy." Bound `_PARSE_RETRIES` unchanged.
- **`_directive_for` (`:436-471`)** — the trailing "Ukonči … `<<<PIPELINE_STATUS>>>` blokom" becomes a transport-
  agnostic "Ukonči štruktúrovaným stavovým výstupom (F-007 §7.2)" (in-repo brief; the charter §7.2 text is Dedo's).
- **`dialogue.py:215`** — unpack the 3-tuple, pass no schema, ignore `structured_output` (Gate E is free-text).

## 4. CR breakdown (build order)
- **R3-a (transport):** `claude_agent.py` `json_schema` param + `--json-schema` arg + `structured_output`
  extraction (both modes) + the 3-tuple return; thread it through `_split_claude_result` (`orchestrator.py:111`)
  + update the `dialogue.py:215` caller to unpack the 3-tuple; **update ALL `invoke_claude` test mocks (5 test
  files) to the 3-tuple** (mandatory — else the suite breaks).
- **R3-b (parse):** `PIPELINE_STATUS_JSON_SCHEMA` export + `parse_structured_output` + wire `invoke_agent` to
  prefer it with the fence fallback; transport-agnostic retry re-prompt + brief text.
- **R3-c (tests):** see §6.
- **Charters (§7.2):** Dedo updates `.claude/agents/**` separately (NOT this CR).

## 5. Seams to preserve (from grounding)
- **The fence parser is the FALLBACK — do NOT delete it.** Both paths feed the SAME Pydantic validation.
- `_PARSE_RETRIES`, the retry loop condition, cross-attempt `_DispatchMetrics`/`parse_attempts` accounting —
  unchanged (else the resilience SLA + token accounting break).
- The `ParseFailure` shape (`reason`/`usage`/`timing`/`lost_work`) — unchanged; R1-c `lost_work` still composes
  (the `:1986` guard untouched).
- `blocked`-escalation + the Coordinator engine-failure relay (`_coordinator_relay_engine_failure`) — unchanged.
- Both streaming + non-streaming envelope paths must extract `structured_output`.
- The schema passed MUST be derived from `PipelineStatusBlock` (no hand-written drift); a too-strict schema the
  model can't satisfy is caught by D2's fallback + the retry, never a silent loss.
- `dialogue.py` Gate E free-text is unaffected (no schema → `structured_output` None).

## 6. Test points
- Agent emits valid `structured_output` → `parse_structured_output` returns the block; no fence needed.
- `structured_output` absent (no schema / older path) → fence fallback parses the result text (existing tests stay green).
- `structured_output` present but schema-invalid → `ParseFailure` → parse-retry → escalate (degradation intact).
- 3-tuple: `invoke_claude` returns `(text, usage, structured_output)`; `dialogue.py` unpacks + ignores the 3rd.
- Metrics: `parse_attempts`/usage/timing accumulate across retries exactly as before.
- R1-c composition: a timeout still yields `lost_work` on the `ParseFailure` (no regression).
- `--json-schema` arg is built only when a schema is passed (Gate E invocation carries no `--json-schema`).

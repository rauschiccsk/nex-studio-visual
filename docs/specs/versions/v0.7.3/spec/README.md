# v0.7.3 — task_plan incremental generation + cockpit legibility

> **Fix package** surfaced by the nex-asistent autonomous-build test (2026-06-18).
> Two independent CRs. **Both touch `backend/services/orchestrator.py` → build/verify SEQUENTIALLY, CR-1 first.**
> Out of this version (Dedo-owned, not Implementer): F-007 spec amendment (CR-1), `templates/coordinator-charter.md` template edit if not covered below, KB driver-doc reconciliation in `/home/icc/knowledge`.

---

## CR-1 — task_plan: incremental EPIC→FEAT→TASK generation

### Problem

The `task_plan` stage asks the Designer to emit the **entire** EPIC→FEAT→TASK tree in **one** structured-output turn. `orchestrator.py` invokes the agent with `PIPELINE_STATUS_JSON_SCHEMA` (grammar-constrained), whose nested `plan` (TaskPlan → epics → feats → tasks, `pipeline_status.py`) forces the whole tree into one object. On a large design (nex-asistent after Gate E: many feats) the model produced epics+feats but **dropped/truncated the per-feat tasks** (`TaskPlanFeat.tasks` `min_length=1`), so validation failed; `_PARSE_RETRIES=2` re-attempted the **same whole tree** and failed identically → `parse_exhaustion`. The retry loop was built for transient JSON typos, not a size/depth problem.

### Design — bounded multi-pass loop, then the existing single write

Generate incrementally, accumulate a full in-memory `TaskPlan`, then call the **unchanged** `_write_task_plan`:

1. **New `_run_task_plan_round(...)`** mirroring `_run_gate_e_round`, dispatched from `run_dispatch` via an **early-return for `stage == "task_plan"`** (right after the build branch). The single generic invoke at `orchestrator.py:~2009` no longer handles task_plan.
2. **Pass 1 — skeleton:** Designer emits **EPIC + FEAT only** (epic title + `module_id`; feat title/description/estimated_minutes; plus `cross_cutting_rules`), **NO tasks**, validated against a narrowed `TaskPlanSkeleton` schema. Collect ordered `(epic_idx, feat_idx, feat_title)`.
3. **Passes 2..N — per-feat tasks:** for **each** feat in skeleton order, one bounded `--resume` turn (keeps the full design + skeleton in context) emitting **only that feat's `tasks[]`** (title, task_type, description, checklist_type, priority, estimated_minutes) against a narrowed `TaskPlanFeatTasks` schema. Accumulate onto the in-memory feat.
4. **Assemble** the full `TaskPlan` in **skeleton order** (not arrival order — `_write_task_plan`'s MAX+1 numbering must match what the Director reviews), synthesize a final `PipelineStatusBlock(stage="task_plan", kind="gate_report", plan=<full>, cross_cutting_rules=...)`, and call the **unchanged** `_write_task_plan` (`orchestrator.py:769`). Then run the existing settle (coordinator synthesis → `awaiting_director`, `orchestrator.py:~2127`).
5. **Narrowed-pass plumbing — dedicated helper, `invoke_agent` UNTOUCHED** (resolves the parse-back / return / message-record gap the Implementer flagged 2026-06-18). The narrowed passes do **NOT** go through `invoke_agent` / `invoke_agent_with_parse_retry` (those assume a `PipelineStatusBlock` and record a standard message) — leave them **and `PIPELINE_STATUS_JSON_SCHEMA` byte-identical**. Instead add one helper used **only** by `_run_task_plan_round`:
   - **`_invoke_plan_pass(db, state, *, prompt, json_schema, parser, label, on_event, on_message, resume_session)`** — calls the existing low-level `invoke_claude(... json_schema=<narrowed>, resume=<task_plan designer session>)` (R3 path, already supports `--json-schema`), extracts the `structured_output` envelope field, and parses it with `parser` under the **same parse-retry policy** (`_PARSE_RETRIES=2`, per pass).
   - **Return:** the parsed **narrowed model** (`TaskPlanSkeleton` / `TaskPlanFeatTasks`) on success; on retry-exhaustion **`ParseFailure`** → the round's fail-closed HALT (point 6).
   - **Parsers (new, `pipeline_status.py`):** `parse_task_plan_skeleton` + `parse_task_plan_feat_tasks`. The narrowed models are **separate types** (`TaskPlanSkeleton` has its own no-`tasks` feat type); **do NOT** relax `TaskPlanFeat.tasks min_length=1` (F-007 §9 "schéma nemení").
   - **Message recording:** the helper records a concise **synthetic audit `pipeline_message` per pass** (author=`designer`, kind=`note`) carrying the same usage payload `invoke_agent` records (preserve `on_message` broadcast + WS-D metrics): skeleton → `"Plán — kostra: N epík, M funkcií; úlohy sa dopĺňajú per funkcia."`; per-feat → `"Plán — funkcia „<feat>": K úloh."`. No `summary`/`awaiting` (these are not status blocks).
   - **No `json_schema_override` on `invoke_agent`** — that idea is dropped; the helper takes the schema directly, so `invoke_agent` keeps **zero** new params.
6. **Limits / fail-closed:**
   - `MAX_PLAN_FEATS` cap (new constant) on total feats; if exceeded, HALT to `blocked` with a clear coordinator relay (consistent with F-007 coarse-grained "module ≈ task").
   - Per-pass parse-retry stays `_PARSE_RETRIES=2`, now applied **per bounded pass**.
   - **Skeleton** exhaustion → the **same `parse_exhaustion` relay** path as today.
   - A **single per-feat pass** exhausting → HALT to `blocked` via the engine-failure coordinator relay **naming the feat**, writing **nothing** (no half-plan).
   - **Envelope-loss parity (R1/R4) — distinguish a `ClaudeAgentError` (timeout/crash) from a genuine PARSE exhaustion.** On a `ClaudeAgentError` in ANY plan pass, follow the **same R1 handling as `invoke_agent`** — run `_audit_lost_work` and settle to **`awaiting_director`** ("práca mohla byť zapísaná — over a pokračuj"), **NOT** a `blocked` dead-end (task_plan was never carved out of R1). `block_reason` MUST be accurate: `parse_exhaustion` **only** for genuinely unparseable structured output; `agent_error` for a timeout/crash (`ClaudeAgentError`); `system_error` for the `MAX_PLAN_FEATS` cap. Do **not** mislabel a timeout as `parse_exhaustion`. (Found by the CR-1 adversarial audit 2026-06-18.)
7. **No validator relax needed.** Because the narrowed passes use the dedicated helper (point 5), **not** `invoke_agent`, the `~283` task_plan plan-required guard is **never hit** during the passes. Only the **final assembled** `PipelineStatusBlock` (full non-empty plan) goes through the normal validate → `_write_task_plan` path — **guard unchanged**. Keep `_write_task_plan`'s empty-plan backstop and **assert non-empty on the assembled block**.
8. **TEXT/FENCE EXTRACTION — the narrowed passes must NOT rely on `structured_output` (LIVE ROOT-CAUSE 2026-06-18).** In this CLI environment `claude --json-schema` does **NOT** return a `structured_output` envelope field — the model emits the content as TEXT (a markdown ```json fence). `invoke_agent` only works because it **falls back to `parse_status_block(stdout)`** on the `<<<PIPELINE_STATUS>>>` fence (orchestrator.py:1092-1097); `structured_output` is silently dead. `_plan_pass_once` had **no fallback** (`structured is None → ParseFailure`) → every narrowed pass failed → `parse_exhaustion`. **FIX — mirror invoke_agent's working path for the narrowed passes:**
   - The narrowed **directives** must instruct the model to emit the narrowed JSON inside a **dedicated sentinel fence** (e.g. `<<<TASK_PLAN_JSON>>>` … `<<<END_TASK_PLAN_JSON>>>`) with the **EXACT** narrowed-schema field names — `epics`/`feats` (NOT `features`), `title`, `module_id`, `description`, `estimated_minutes` for the skeleton; `tasks[]` with `title`/`task_type`/`description`/`checklist_type`/`priority`/`estimated_minutes` for per-feat — and **no extraneous fields** (no `project`/`version`/`level`/`id`). Include a tiny concrete example in the directive.
   - `_plan_pass_once`: try `structured_output` first (forward-compat if the CLI ever returns it); when `None` (always, today), **fall back to extracting the sentinel-fenced JSON** from stdout → `json.loads` → `parser`. Parse-retry still applies per pass.
   - The two parsers validate into the narrowed models; be **tolerant** of the most likely drift (accept `features` as an alias for `feats`; ignore unknown keys) but require the core shape.
   - **REAL test (the gap that masked this):** add a test where the fake claude returns **TEXT with the sentinel fence and NO `structured_output`** (`structured=None`) — exercising the real-env path. The existing dict-returning FakeClaude tests are insufficient alone; keep them but add the text-fence case for skeleton AND per-feat.

### Files (from grounded design)

- `backend/services/orchestrator.py`: `~2009` single dispatch → early-return into `_run_task_plan_round`; `~470` `_directive_for` task_plan branch split into skeleton + per-feat prompts; `769` `_write_task_plan` **unchanged** (fed the accumulated full plan); **new `_invoke_plan_pass` helper** (point 5) — `invoke_agent`/`invoke_agent_with_parse_retry` **unchanged**; `~2113` settle reached only after assembly; `221` `_PARSE_RETRIES` semantics now per-pass.
- `backend/services/pipeline_status.py`: add `TaskPlanSkeleton` (its own no-`tasks` feat type) + `TaskPlanFeatTasks` (tasks-only) models + their `model_json_schema()` + the two parsers `parse_task_plan_skeleton`/`parse_task_plan_feat_tasks`. `TaskPlanFeat` and the `~283` plan-required guard **unchanged**.
- `backend/services/{epic,feat,task}.py`: `create()` reused **unchanged** after accumulation.

### Acceptance criteria

- A large design (≥ the nex-asistent feat count) yields a **complete** EPIC→FEAT→TASK plan (every feat has ≥1 task), written by the unchanged `_write_task_plan`, settling to `awaiting_director`.
- Per-pass parse-retry recovers a single-feat typo without re-emitting the whole tree.
- `invoke_agent` / `invoke_agent_with_parse_retry` / `PIPELINE_STATUS_JSON_SCHEMA` are **byte-identical** (untouched); the narrowed passes go through the dedicated `_invoke_plan_pass` helper. Each pass records a synthetic audit message (skeleton + per-feat) so the trail/metrics are preserved.
- Fail-closed verified: a forced per-feat failure HALTs to `blocked` naming the feat and writes **no** Epic/Feat/Task rows.
- No DB schema / migration change. Existing task_plan tests updated; new tests for the multi-pass loop + fail-closed.

### Out of scope (Dedo)

`docs/specs/versions/v0.2.0/spec/F-007-task-plan-node.md` §5 + §9 amendment (F-007 currently specifies one plan payload in one block) — Dedo amends, not the Implementer.

---

## CR-2 — cockpit legibility & formatting (Director-facing comms + SK spellcheck + decision-needed prominence)

### Problem

1. **Monolithic prose:** Coordinator→Director messages render as one same-color paragraph, hard to scan. Root cause is **generation**, not rendering: of the three Director-facing prompt builders, only `_coordinator_synthesis` asks for structured markdown (and even it has no one-line headline); `_coordinator_relay` and the `verify_done` judge ask for **no formatting** → monolithic prose. (FE already renders markdown via `ReactMarkdown`+`remarkGfm`, XSS-safe.)
2. **SK spellcheck:** the single Director composer `<textarea>` (`PipelineActionBar.tsx:166`) has no `lang` → the browser's English dictionary underlines Slovak.
3. **Decision-needed invisibility:** `document.title` is never set; `awaiting_director` is signalled only by a thin one-line banner → a healthy board reads as "stuck".

### Design

**A. Generation (`backend/services/orchestrator.py`)**
- Add one shared Slovak constant `_DIRECTOR_FORMAT_BRIEF` and **append it to ALL FIVE Director-facing coordinator prompts** (recipient=`director`) — not just three (audit 2026-06-18 found 2 more): `_coordinator_synthesis`, `_coordinator_relay`, the `verify_done` judge, **`_coordinator_relay_engine_failure`** (the engine-failure/HALT escalation the Director reads at a block), and **`_coordinator_review_gap`** (the Gate-E gap recommendation). It instructs:
  > Začni **jednoriadkovým nadpisom** (`## `) — najpodstatnejšie rozhodnutie/stav v jednej vete (TL;DR). Potom krátke sekcie, **tučným** zvýrazni kľúčové pojmy, a pre možnosti/kroky/riziká použi odrážkové zoznamy. Nikdy nepíš jeden monolitný odsek. Slovensky.
- The headline lives **INLINE in `summary`** (the rendered body). **No schema change** — the `<<<PIPELINE_STATUS>>>` contract / R3 grammar stays intact.
- Mark the genuinely Director-facing recorded turns with `extra_payload={"is_director_brief": true}` so the FE gives them the prominent rail (today only `_coordinator_synthesis` sets `is_synthesis`). **GATING (audit 2026-06-18):** set `is_director_brief` ONLY on a turn the Director actually reads at a decision point. For `verify_done` this means **only when the verify settles to `awaiting_director`/`blocked`** — NOT on a gate_report PASS (the synthesis is the Director-facing turn there) and NOT on the auto-return-loop retries (worker re-dispatched, state stays `agent_working`). relay / relay_engine_failure / review_gap are Director-facing by construction → tag them; never tag a worker/internal turn.
- **Unify the stale `§7.2` cross-ref repo-wide.** All ~21 prompts in `orchestrator.py` cite a non-existent charter `§7.2` for the `<<<PIPELINE_STATUS>>>` block; the protocol actually lives in **`F-007-orchestration-cockpit.md §5.3`**. Replace every `(§7.2)` status-block citation with the unambiguous `(F-007-orchestration-cockpit.md §5.3)` (use the full filename — a bare `F-007 §5.3` is ambiguous vs `F-007-task-plan-node.md`).

**B. Rendering (`frontend/src/components/cockpit/PipelineMessageBubble.tsx`)**
- Extend the existing prominent-rail path (today `is_synthesis` / `is_autonomous`, `~:59-93`) to also fire for `is_director_brief` (relay/verify briefs) with a "Na rade" style label (`labels.ts`).
- Tighten the `prose` styles (`~:101-106`) so **bold** and **bullet lists** render distinctly (add `prose-strong` / `prose-ul` / `prose-li`). No new dependency.

**C. SK spellcheck (`frontend/src/components/cockpit/PipelineActionBar.tsx:166`)**
- Add `lang="sk"` (+ explicit `spellCheck`) to the composer textarea — one edit covers every Director text path (return/answer/ask/return-with-comment share this composer). Code comment: correctness depends on the browser having a SK dictionary, but `lang="sk"` is the correct app-side declaration.

**D. Decision-needed prominence**
- `frontend/src/pages/CockpitPage.tsx`: `useEffect` keyed on `board.state.status` + `current_stage` → set `document.title = "(•) Na rade: Director — " + STAGE_LABELS[stage]` when status is `awaiting_director` or `blocked`; restore a neutral base title for `agent_working`/`done`/`paused`/null **and on cleanup/unmount** (capture base title in a ref so the marker never leaks to other pages).
- `frontend/src/components/cockpit/ExchangePanel.tsx` (`~:82,139-143`): when status is `awaiting_director`/`blocked`, render the banner as a **sticky, high-contrast CTA** (`sticky top-0 z-10`, solid warning bg + fg, `text-sm font-semibold`, left accent, glyph). Keep the low-key tonal banner for `agent_working`/`done` (no false alarm). Respect light+dark token discipline (`text-X-700 dark:text-X-300`, no raw pastels).

**E. Charter (`templates/coordinator-charter.md`)**
- Add a §5 subsection "Formát správ Directorovi" codifying the same contract (headline-first markdown, sections, bold, bulleted options/risks) + align the §9 DONE skeleton to lead with the headline. (Durable source for future projects; note this does **not** retrofit already-created projects' charters — the central orchestrator-prompt change in **A** is what fixes nex-asistent immediately.)

### Acceptance criteria

- Synthesis, relay, and verify Director-facing messages all begin with a one-line `## ` headline, use sections/**bold**/bullets, and render with the prominent rail.
- Typing Slovak in the composer no longer underlines as misspelled (verify in-browser with `lang="sk"`).
- At `awaiting_director`/`blocked`: tab title shows `(•) Na rade: Director — <stage>` and reverts on `agent_working` and on navigate-away; the banner is a prominent sticky CTA.
- No change to `PipelineStatusBlock` schema / `<<<PIPELINE_STATUS>>>` contract. FE vitest (`PipelineMessageBubble`, `ExchangePanel`, labels) updated; build + lint clean (FE is a prod nginx bundle → needs `docker compose build frontend`).

### Out of scope

- Optional schema-backed `headline`/`severity` fields (heavier; deferred — inline markdown achieves the visible outcome).
- The unused custom `SlovakTextarea`/`spellchecker.ts` (dead-but-built; not wired here).
- Retrofitting already-created projects' coordinator charters.

---

## Deferred / not-in-this-version (decided 2026-06-18, Director approved)

- **R-C** (apply_coordinator_recommendation advance-on-verify-pass): **deferred** — design item that overlaps the existing `approve`-advance and needs a non-build PASS verdict signal that doesn't exist; changing it now would perturb the live nex-asistent test.
- **Create-project port auto-suggestion**: **no code bug** — logic is correct (D-020 band, skips used+reserved); any "NEX Test echo" is a live-DB `reserved_port_ranges` config gap, fixed via a Settings value at deploy.
- **KB driver doc reconciliation** (asyncpg vs pg8000): **docs-only**, in `/home/icc/knowledge` (outside this repo); Dedo applies it + RAG reindex separately. Truth = pg8000 + SQLAlchemy ORM; asyncpg refs are NEX-Command-scoped.

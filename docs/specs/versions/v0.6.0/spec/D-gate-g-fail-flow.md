# v0.6.0 Cockpit Hardening — Pillar D: gate_g FAIL flow (Class I)

> **Director-approved boundary (2026-06-13).** The LAST cockpit-hardening slice. Fixes the two coupled defects
> seen in the live NEX Inbox v1.0.1 gate_g pipeline:
> **(1)** the verify-judge auto-returns ANY Coordinator "blocked" verdict to the Auditor — even a scope/design
> question the Auditor cannot fix — so an already-answered scope question loops forever and the pipeline never
> settles; **(2)** a FAIL verdict blindly re-gates from `gate_a` (full Designer→Customer→task_plan→build re-run)
> even when only the implementation of specific tasks failed audit.
> Foundation (waterfall): a scope gap surfaced at the final audit is a DESIGN-QUALITY signal — it escalates ONCE
> to the Director / re-gates to the Designer, it is never looped against the Auditor. Pillars A (synthesis), C
> (per-task reporting), B (autonomous decision) are LIVE; this builds on all three.
> **Revision 2 (post-red-team, 2026-06-13):** the first draft's Fix-2 directive read was architecturally wrong
> (the classifying directive lives on a `question`-kind message at gate_g, not a `gate_report`), the
> text-overlap dedup was fragile, and the escalate-once settle could strand the Director on a synthesis
> ParseFailure. This revision resolves all of it; see *Resolved decisions* + *Implementation seams*.

Shipped as **two CRs** under this one design:
- **CR-NS-056 — Fix 1** (verify-judge mechanical-vs-scope classification + escalate-ONCE-per-iteration + the FE
  scope-escalation rendering). Fixes the acute v1.0.1 loop. **Ships + verifiable ALONE** — its scope detection
  is in-process (the directive is passed from `verify_done`, no DB read), and it stops the loop + gives the
  Director a clarify path. The real-design-gap *resolution* (FAIL→gate_a) is completed by Fix 2.
- **CR-NS-057 — Fix 2** (Coordinator-inferred targeted re-gate; the verdict stays the Director's). **Depends on
  the gate_g classifying-directive helper introduced here** (§F2.1) and completes the design-gap resolution by
  rendering the FAIL→target verdict at gate_g/blocked too.

---

## Resolved decisions (first principles — these close the red-team findings)

1. **Classification signal = the existing Pillar-B triage vocabulary** (no new classifier, no schema field). A
   verify-judge "blocked" turn already carries a `coordinator_directive` (`triage_class`, `proposed_action`). A
   failure is **SCOPE/DESIGN** (Auditor-unfixable → escalate) iff
   `triage_class == "director_decision"` **OR** `proposed_action == "coordinator_route_to_designer"`. Everything
   else — a missing directive, a mechanical `proposed_action` (reset/clear), a `spec_problem`/`programmer_guidance`/
   `nex_studio_bug` flag, a P-2/missing-citation defect — is **MECHANICAL** (Auditor CAN fix → the existing
   auto-return loop, unchanged, bounded by `_VERIFY_RETRIES=2`). **Fail-open:** no directive ⇒ mechanical.
   *(Note: `spec_problem` is MECHANICAL for the Fix-1 verify branch — it is an Auditor-fixable citation/spec-ref
   defect — but it is a DESIGN signal for the Fix-2 re-gate target; the two uses are deliberate and consistent.)*
2. **Escalate settle = `blocked`** with the scope question + a Coordinator synthesis (Pillar A). The loop is
   BROKEN at the first scope detection (never re-returns to the Auditor). **Per-iteration cap (replaces the
   fragile text-overlap dedup):** a scope question escalates to the Director **at most ONCE per gate_g
   iteration**. On a SECOND scope flag in the same iteration (after the Director already responded once), the
   pipeline does NOT loop — it settles to `awaiting_director` so the Director makes the definitive call. This is
   the Pillar-B per-task-cap pattern (no text matching, no forced PASS, no answer-channel ambiguity).
3. **The Director resolves a gate_g scope question two ways, both surfaced at the `blocked` state (Fix 2 renders
   the verdict here):** (a) **answer** = "the Auditor misread; here is the clarification, NO design change" →
   re-audit (the clarification is fed into the next verify prompt; the cap guarantees no loop); (b) **FAIL→gate_a**
   = "this is a real design gap" → re-gate to the Designer (routes the gap to design — the waterfall response).
   This reconciles "route to Designer" with the answer-back-to-Auditor mechanism: the Auditor is re-run ONLY for
   a no-design-change clarification.
4. **Re-gate target inference (Fix 2):** read the **latest gate_g classifying directive** (§F2.1) —
   `spec_problem` / `director_decision` / `coordinator_route_to_designer` → **`gate_a`** (design re-gate, the
   waterfall design-quality response); else (programmer_guidance / nex_studio_bug, OR **no directive**) →
   **`build`** (re-run the build — the conservative default for a Director-initiated FAIL on a PASS-verified
   audit). The Director always overrides via chips. **Truth table** is in §F2.1.
5. **`build` re-gate crux:** at gate_g no task is `failed`/`in_progress` (the build→gate_g guard drains them); via
   `approve` all tasks are `done`, via `end_build` some may remain `todo`. `_reset_done_tasks_for_regate` flips
   `done`→`todo` (todo untouched), so the whole build re-runs. The gate_g audit findings are threaded into the
   re-run brief (§F2.2) so the re-run is NOT blind. Per-task targeting (only the failing tasks) is deferred (the
   gate_g aggregate report names no individual tasks — needs an Auditor-charter change; flagged, NOT this slice).
6. **The verdict stays the Director's.** Fix 2 only changes the FAIL handler's DEFAULT `entry_stage` (was the
   literal `"gate_a"`) to the inferred target + surfaces the proposal. An explicit `payload.entry_stage` always
   wins; the `STAGE_ORDER` guard is unchanged.

---

## CR-NS-056 — Fix 1: verify-judge mechanical-vs-scope, escalate-once-per-iteration

### §F1.1 — plumb the directive out of `verify_done`
`verify_done` (orchestrator.py:1351-1400) returns `Optional[str]` and discards the judge's `coordinator_directive`
at the blocked branch (:1398-1399). Change its return to `tuple[Optional[str], Optional[dict[str, Any]]]` =
`(reason, directive)`, mirroring `_coordinator_relay`'s `(relay_text, directive)` contract (:1408, 1459-1461).
Build the dict via `judgment.coordinator_directive.model_dump(mode="json") if judgment.coordinator_directive is
not None else None` (the expression at :995-999, :1460). Returns: PASS → `(None, None)`; mechanical
disk-deliverable fail → `(mech, None)`; ParseFailure → `(reason, None)`; blocked →
`(f"coordinator flagged: {judgment.question or judgment.summary}", <directive-dict-or-None>)`.

### §F1.2 — the scope classifier (in-process, no DB read)
New module-level helper beside `_coordinator_directive_executable` (~orchestrator.py:2037):
```
def _verify_reason_is_scope(directive: Optional[dict[str, Any]]) -> bool:
    if not directive:                       # fail-open: no triage ⇒ mechanical
        return False
    return (directive.get("triage_class") == "director_decision"
            or directive.get("proposed_action") == "coordinator_route_to_designer")
```
Reuses the two Pillar-B scope discriminators verbatim (:2050; :1992-1995, 2330). No new enum, no schema field.

### §F1.3 — branch in `_verify_with_retries`
`_verify_with_retries` (orchestrator.py:1843-1895) return type → `tuple[Optional[str], bool]` = `(reason,
is_scope)`. (a) at :1854 and the in-loop re-verify :1894: `reason = await verify_done(...)` →
`reason, directive = await verify_done(...)`. (b) BEFORE the while-loop AND after each in-loop re-verify: if
`reason is not None and _verify_reason_is_scope(directive)` → STOP the loop, return `(reason, True)`. (c) **Convert
EVERY existing return to the tuple shape** — the two mid-loop early returns at **:1890** (WS-E exhaustion) and
**:1892** (non-gate_report give-up) AND the final loop-exit fall-through at **:1895** (`return reason`): all
become `return (reason, False)`. After the change the function has NO bare-`str`/`None` return path (grep the
function for every `return` and confirm each is a 2-tuple). The mechanical path is **behaviorally** unchanged (the
auto-return loop fires up to `_VERIFY_RETRIES`; message recording + the bound untouched).

### §F1.4 — caller settle (gate_report handler, orchestrator.py:1618-1631)
`reason = await _verify_with_retries(...)` → `reason, is_scope = await _verify_with_retries(...)` at :1619. Split
the `reason is not None` branch:
- **`is_scope` False (mechanical, exhausted):** TODAY's behavior verbatim — `status="blocked"`,
  `next_action="Fáza '{stage}' neprešla overením — pozri správy Koordinátora a rozhodni."`
- **`is_scope` True AND `state.current_stage=="gate_g"`** (the scope branch is GATED to gate_g — at gate_a..gate_d
  a director_decision/route_to_designer verify flag falls through to the mechanical auto-return, preserving today's
  behavior; generalizing scope-escalation to earlier gates is out of scope): apply the **per-iteration cap**
  (§F1.5). The cap counter `_scope_escalations_this_iteration` INCLUDES this turn's just-recorded scope question
  (it was recorded by `invoke_agent` inside `verify_done` BEFORE this caller runs — :962), so the guard is `<=`,
  treating the current question as the one allowed escalation: if `_scope_escalations_this_iteration(db,
  version_id) <= _MAX_SCOPE_ESCALATIONS_PER_ITERATION` (=1) → **escalate ONCE** (1st flag: count==1, 1<=1 True):
  fire `_coordinator_synthesis(db, state, trigger=f"fáza '{stage}' — otázka rozsahu", on_message=on_message)`
  FIRST (while `current_actor` is still `auditor`, so the guard at :1211-1212 lets it fire), THEN
  `status="blocked"`, `current_actor` STAYS `auditor`, `current_stage` STAYS `gate_g`, `next_action` = the scope
  question. ELSE (2nd flag this iteration: count==2, 2<=1 False — the Director already responded once) → **do NOT
  loop:** `status="awaiting_director"` (the verdict block renders, Fix 2), `next_action="Audit označil otázku
  rozsahu druhýkrát — rozhodni: PASS alebo FAIL → fáza."`
- The scope question is already recorded as a coordinator→director message by `invoke_agent` (:962-1009) with the
  directive in payload, so it is on the board regardless of the synthesis outcome (see §F1.7 for the FE rule that
  makes it answerable even if the synthesis ParseFails).

### §F1.5 — the per-iteration escalation cap (the loop guarantee)
New pure helper near `_latest_coordinator_directive` (~orchestrator.py:2035):
```
def _scope_escalations_this_iteration(db, version_id) -> int:
    # count coordinator scope-question messages at stage 'gate_g' in the CURRENT iteration:
    #   author=="coordinator" AND kind=="question" AND stage=="gate_g"
    #   AND directive is a scope class: triage_class=="director_decision" OR proposed_action=="coordinator_route_to_designer"
    #   AND seq > <seq of the latest kind=="verdict" message for this version, else 0>   # iteration boundary (verdict
    #       recorded at orchestrator.py:3185-3194; iteration++ at :3204 on FAIL)
    # GUARDRAIL: the coordinator_directive KEY is always present, JSON-null for a directive-less turn (:995-999).
    #   Filter the triage class in SQL (payload['coordinator_directive']['triage_class'].astext == 'director_decision'
    #   — JSON-null index → SQL NULL → row excluded, null-safe), OR in Python guard
    #   `((row.payload or {}).get("coordinator_directive") or {}).get("triage_class")` — never `.get(k, {}).get(...)`
    #   (that returns None.get on a JSON-null value and raises).
```
Constant `_MAX_SCOPE_ESCALATIONS_PER_ITERATION = 1` near `_VERIFY_RETRIES` (:180). The verdict-message seq is the
iteration boundary because a `verdict` is exactly what increments `state.iteration` (:3204); count from 0 on the
first iteration. This makes the cap per-iteration without an `iteration` column on the message. **Off-by-one
note (load-bearing):** the counter is evaluated AFTER `verify_done` already recorded this turn's scope question
(:962), so it INCLUDES the current escalation. The 1st flag → count==1; the 2nd → count==2. Hence §F1.4's guard
is `<= _MAX` (not `<`): `1<=1` escalates the first, `2<=1` caps the second. This differs from Pillar B's `>= cap`
check (which runs BEFORE recording its marker, :2331/:2334) precisely because here the marker is recorded by the
dispatch path first — verify the count includes the just-recorded question (the §F1.8 test pins first→escalate,
second→cap).
**`answer`/`ask`/`return` are ALREADY offered at any `status=="blocked"`** (determine_available_actions
orchestrator.py:278-279) — NO action-set change is needed (verified; §F1.7 only covers the FE).

### §F1.6 — feed the prior scope Q&A into the verify prompt (reduces hitting the cap)
New pure derivation helper just above `verify_done` (~orchestrator.py:1350), modeled on `_latest_designer_answer`
(:570-583) / `_gate_e_open_findings` (:532): `_prior_scope_qa(db, version_id) -> list[tuple[str, str]]` — at
stage `gate_g`, in the current iteration (seq > the iteration boundary, as §F1.5), pair each coordinator
scope-question with the FIRST director-authored response of greater seq (kind in {`answer`, `return`, `question`}
— ANY channel the Director used; this is prompt CONTEXT, not the loop guarantee, so the channel breadth is safe).
In `verify_done` before building the prompt, if non-empty, append a Slovak `prior_scope_block` between the P-2
line (:1375-1376) and the directive-emit instructions (:1377-1381): numbered `Q: … / Director: …` pairs + *"Na
tieto otázky rozsahu už Director reagoval — NEoznačuj ich znova ako blocker, ak nepribudol NOVÝ problém alebo
mechanická chyba (chýbajúca citácia / P-2)."* When empty the prompt is **byte-identical** to today. (The cap in
§F1.5 is the hard guarantee; this only reduces how often it is hit.)

### §F1.7 — FE (Fix 1): make the scope escalation answerable even on a synthesis ParseFailure
A gate_g `blocked` state must render the **"Odpoveď"** composer (clarify path), NOT collapse to "Skús znova".
Today `isErrorBlock = blocked && lastMessage.author==="system"` (ExchangePanel.tsx:74) and `questionBlock = blocked
&& !isErrorBlock && !gateE` (PipelineActionBar.tsx:126). If the §F1.4 synthesis ParseFails,
`_record_internal_turn_parse_failure` records a `system` note as the LAST message (orchestrator.py:1167-1182) →
`isErrorBlock` flips true → only "Skús znova" renders → the Director is STUCK. **Production FE changes (pinned to
the `current_stage==="gate_g"` proxy, since PipelineActionBar receives only `state`/`availableActions`/flags, not
the message thread — at gate_g a blocked state is ALWAYS a coordinator scope escalation, so the stage proxy is
exact):**
1. **questionBlock override:** `questionBlock = blocked && !gateE && (!isErrorBlock || state.current_stage==="gate_g")`
   (PipelineActionBar.tsx:126) — a gate_g blocked state renders "Odpoveď" regardless of a trailing `system` note.
1b. **errorBlock gate (MANDATORY companion to 1):** the SEPARATE errorBlock render (PipelineActionBar.tsx:535,
   `{errorBlock && allowed("return") && (...)}` → the "Skús znova" retry) must ALSO exclude gate_g — add
   `&& state.current_stage!=="gate_g"`. Without this, a gate_g scope escalation + a synthesis ParseFailure (system
   note last → `isErrorBlock` true) renders BOTH "Odpoveď" AND "Skús znova" (contradictory: a blind re-dispatch
   alongside the answer). At gate_g the answer/verdict path is the only correct resolution; a genuine auditor crash
   at gate_g is still recoverable via "Odpoveď"/Vrátiť. (The gate_b error-block tests stay green — they run at
   gate_b, unaffected by the gate_g exclusion.)
2. **Suppress the rubber-stamp one-click** (PipelineActionBar.tsx:191-201): add `&& state.current_stage!=="gate_g"`
   to its guard — a scope/design question must get a real typed answer, never "Schvaľujem, pokračuj".
3. **WhosTurnBoard label:** `decisionType()` checks `actions.includes("verdict")` at WhosTurnBoard.tsx:15 BEFORE
   the blocked branch at :17 — and Fix 2 adds `verdict` to the gate_g/blocked action set, so the verdict label
   would win. Add, BEFORE the :15 verdict check: `if (stage === "gate_g" && status === "blocked") return "Odpovedz
   alebo rozhodni";` so a gate_g scope escalation reads as answer-or-decide, matching what the action bar offers
   (clarify via Odpoveď + Fix-2's FAIL→target). Add a WhosTurnBoard test for this case.
The Coordinator's analysis renders via the existing Pillar-A synthesis rail (PipelineMessageBubble.tsx:57,66-67).

### §F1.8 — tests (Fix 1)
NEW (backend): `test_verify_reason_is_scope_predicate` (unit: None→False; director_decision→True;
route_to_designer→True; spec_problem+reset→False; programmer_guidance→False); `test_verify_done_returns_directive`
(2-tuple; directive on blocked, `(None,None)` on PASS); `test_gate_g_verify_scope_question_escalates_once`
(director_decision/route_to_designer judge → ZERO system→auditor `return` messages, status=`blocked`,
current_actor=`auditor`, current_stage=`gate_g`, synthesis fired, exactly one escalation);
`test_gate_g_scope_escalation_capped_second_time` (a 2nd scope flag in the same iteration → status=`awaiting_director`,
NO new escalation/loop); `test_gate_g_verify_mechanical_failure_auto_returns` (P-2 / no-directive blocked →
auto-return loop fires `_VERIFY_RETRIES`, settles `blocked` — behaviorally today); `test_scope_escalations_this_iteration_counts_from_verdict_boundary`
(cap counter resets after a verdict/re-gate); `test_prior_scope_qa_pairs_any_director_channel` (answer/return both
pair); `test_verify_prompt_injects_prior_scope_block` (captured prompt contains the Director's response + the
do-not-re-raise line). NEW (FE): test_PipelineActionBar "(gate_g, blocked) coordinator scope question shows
Odpoveď even with a trailing system note, and NOT the Schváliť-a-pokračovať one-click".
**MUST STAY GREEN (and 2-tuple-updated — see Seam 4):** `test_verify_failure_retries_then_blocks` (:590),
`test_verify_done_prompt_instructs_triage_emit` (:1197), `test_verify_done_judge_parse_failure_visible_note`
(:3293/3301), `test_verify_retry_reemit_parse_failure_visible_note` (:3310/3327),
`test_internal_turn_failure_timing_only_when_usage_none` (:3346/3348 — assert on the UNPACKED reason, not the
truthy tuple), `test_synthesis_at_gate_report_pass` (its `_synthesis_verify_pass` stub :3384 must return
`(None, None)`), `test_coordinator_directive_executable_gate` (:1072), `test_verdict_pass_to_release`,
`test_verdict_fail_regate`, `test_regate_preserves_agent_sessions`, the Pillar-B `_maybe_autonomous_recovery`
tests. **FE MUST-STAY-GREEN (guards the §F1.7 gate_g scoping):** the gate_b error-block tests
(test_PipelineActionBar.test.tsx ~:162 "error-block shows Skús znova" + ~:173 "Skús znova re-dispatches") — they
pass ONLY if the questionBlock/one-click overrides stay scoped to `current_stage==="gate_g"` and do NOT weaken
`isErrorBlock` for a genuine agent crash at other stages.

---

## CR-NS-057 — Fix 2: Coordinator-inferred targeted re-gate (Director keeps the verdict)

### §F2.1 — the gate_g classifying directive + inference
**The directive at a gate_g FAIL lives on a `kind=="question"` message** (invoke_agent maps block.kind
`blocked`→msg_kind `question`, :953); `_latest_coordinator_directive` (:2021-2034) filters `kind=="gate_report"`
so it CANNOT see it, and a directive-less synthesis recorded as `gate_report` would shadow it. New helper near it
(~orchestrator.py:2035):
```
def _latest_gate_g_classifying_directive(db, version_id) -> Optional[dict[str, Any]]:
    # newest PipelineMessage where author=="coordinator" AND stage=="gate_g"
    #   AND the JSONB VALUE at payload['coordinator_directive'] IS NOT JSON-null,
    # ordered by seq DESC, limit 1; return that coordinator_directive dict, else None.
    # kind-agnostic (question OR gate_report) — the directive may ride either.
    #
    # CRITICAL: the non-null filter MUST be in the SQL WHERE BEFORE the LIMIT 1 — invoke_agent ALWAYS writes the
    # "coordinator_directive" KEY (:995-999), JSON-null for a synthesis turn. A naive "ORDER BY seq DESC LIMIT 1,
    # then Python-check non-null" would grab a later synthesis row (key present, value JSON-null) and return None,
    # SHADOWING an older real directive.
    # CORRECT PREDICATE: PipelineMessage.payload["coordinator_directive"].astext.isnot(None)  (compiles to
    #   payload ->> 'coordinator_directive' IS NOT NULL — TRUE for an object value, SQL-NULL/excluded for JSON-null).
    #   Do NOT use .isnot(None) on the JSON expression itself — that tests SQL NULL (key absent), NOT JSON-null value,
    #   so it FAILS to exclude the synthesis row. (Equivalent alternatives: IS DISTINCT FROM 'null'::jsonb, or
    #   fetch-all + pick max-seq with a non-null directive in Python.)
```
Inference helper:
```
def _infer_regate_entry_stage(db, version_id) -> str:
    d = _latest_gate_g_classifying_directive(db, version_id)
    if d and (d.get("triage_class") in ("spec_problem", "director_decision")
              or d.get("proposed_action") == "coordinator_route_to_designer"):
        return "gate_a"          # design/scope gap → full design re-gate (waterfall)
    return "build"               # code-fixable, OR no gate_g directive (Director-initiated FAIL on a PASS audit)
```
**Truth table (gate_g FAIL):** scope-escalation blocked (directive present, design class) → `gate_a`;
scope-escalation blocked (directive present, code class) → `build`; PASS-verified `awaiting_director`,
Director-initiated FAIL (no gate_g directive) → `build`. Always a valid `STAGE_ORDER` member. *(Note: `gate_a` is
reached primarily via the scope-escalation/blocked verdict — see §F2.4 rendering — so the design-gap path is
LIVE, not dead.)*

### §F2.2 — make `build` re-gate actually re-run, with the audit findings
New helper beside `_reset_failed_tasks_to_todo` (:1923-1928), modeled on it but `status=="done"` → `"todo"`:
`_reset_done_tasks_for_regate(db, version_id) -> None` (done→todo for the version; existing `todo` untouched).
Re-run tasks keep `baseline_sha` (re-validate the same work against the corrected understanding; a fresh anchor
is a separate Director `move_baseline`).

**Thread the gate_g findings (not blind) — concrete edit, no Implementer guesswork.** `_run_build_round`
computes `cross_cutting` once at orchestrator.py:2618 (`= _fetch_cross_cutting_rules(...)`, `Optional[str]`,
:2381-2397) and passes it to `_directive_for_build_task(task, cross_cutting, prior_failures)` (:2722), which
injects it only when truthy (:2407). Insert immediately after :2618:
```
if state.is_regate and state.current_stage == "build":
    _gg = _latest_gate_g_findings(db, version_id)
    if _gg:
        cross_cutting = _gg + ("\n\n" + cross_cutting if cross_cutting else "")
```
(The `("\n\n" + cross_cutting if cross_cutting else "")` guards the common `cross_cutting is None` case — no
None-concatenation.)

New pure helper `_latest_gate_g_findings(db, version_id) -> Optional[str]`: the newest Auditor `gate_report` at
stage `gate_g` → its `payload.findings` (+ the latest gate_g classifying directive's rationale), formatted as a
Slovak "audit zistenia z gate_g" block — **BUT ONLY IF that gate_report's `seq` > the seq of the latest
`stage=="task_plan"` message** (else return None). This is the **sticky-`is_regate` guard:** `state.is_regate`
is set True on any FAIL and never reset, so a build reached AFTER a design-class FAIL→gate_a (which re-runs
`task_plan`, creating a newer task_plan message) would otherwise thread STALE pre-redesign findings. Keying on
"no `task_plan` has run since that audit" makes the prepend fire ONLY for a direct FAIL→build re-gate (audit
findings still current) and return None on a gate_a-transitive build (task_plan newer ⇒ findings superseded) —
no new state field needed. Add the §F2.5 test asserting a FAIL→gate_a re-gate's eventual build carries NO prior
gate_g findings.

### §F2.3 — propose the target (Pillar A; board field computed FRESH)
No synthesis-prompt change and **no `regate_entry` payload marker** (avoids the synthesis-kind shadowing the
red-team flagged). The proposal is computed FRESH for the board (§F2.4) via `_infer_regate_entry_stage`. The
existing Pillar-A synthesis (fired at gate_g PASS, and at the scope-escalation per §F1.4) already gives the
Director the Coordinator's plain-Slovak analysis; the board chip + the FAIL→target button carry the WHERE.

### §F2.4 — consume the inference + render the verdict at gate_g (blocked AND awaiting)
- **Board field:** add `regate_proposal?: {entry_stage: PipelineStage; reason?: string} | null` to the
  `PipelineBoardRead` schema (backend/schemas/pipeline.py:58-81) + the board builder
  (backend/api/routes/pipeline.py:80-89) + the FE `PipelineBoard` type (frontend/src/services/api/pipeline.ts
  ~:99). Compute it in the board builder when `state.current_stage=="gate_g" AND state.status IN
  {"awaiting_director","blocked"}`: `entry_stage = _infer_regate_entry_stage(db, version_id)`, `reason` = a short
  Slovak rationale derived from the directive class (e.g. *"návrh/rozsah → späť na dizajn"* vs *"oprava
  implementácie → znova build"*). Absent/null → permissive fallback (the established
  `available_actions`/`gate_e_open_findings` pattern).
- **FAIL verdict handler (orchestrator.py:3199-3207):** line 3200 `entry = payload.get("entry_stage", "gate_a")`
  → `entry = payload.get("entry_stage") or _infer_regate_entry_stage(db, version_id)`. The `STAGE_ORDER` guard
  (:3201-3202) is unchanged. AFTER `state.current_stage = entry` (:3205) and BEFORE `_begin_dispatch` (:3207):
  `if entry == "build": _reset_done_tasks_for_regate(db, version_id)`. A `gate_a` re-gate needs no reset (the
  task_plan write-path drops+rebuilds the epics, :716). Sessions preserved on both targets (D2).
- **FE — render the verdict FAIL→target group at gate_g for BOTH `awaiting_director` AND `blocked`** (the backend
  already allows `verdict` from blocked: determine_available_actions + `_ADVANCING_ACTIONS`). In
  PipelineActionBar.tsx the gate_g verdict block guard (`current_stage==="gate_g" && awaiting && allowed("verdict")`,
  ~:349) broadens to `current_stage==="gate_g" && allowed("verdict")` (blocked-scope also offers verdict). Keep
  PASS at `awaiting` only. New prop `regateProposal`. FAIL group: (a) PRIMARY
  `Verdikt FAIL → ${STAGE_LABELS[regateProposal.entry_stage]}` (hint = `regateProposal.reason`) firing
  `onAction("verdict", {verdict:"FAIL", entry_stage:<target>})`; (b) SECONDARY "Iná fáza" toggle revealing inline
  chips over `REGATE_TARGETS = STAGE_ORDER.filter(s => s!=="kickoff" && s!=="release" && s!=="done" &&
  s!=="gate_g")` (gate_a..build), each firing FAIL with that stage; (c) `regateProposal` absent → plain "Verdikt
  FAIL" with no `entry_stage` (backward-compat). Chips reuse the slate-outline `btn` + `STAGE_LABELS`
  (labels.ts:11-23) — no `<select>`, no modal. Guard a missing `STAGE_LABELS[entry_stage]` → plain "Verdikt FAIL".
- **WhosTurnBoard.tsx:** at gate_g (awaiting OR blocked) with `regateProposal`, an indigo one-liner *"Navrhovaný
  návrat pri FAIL: ${STAGE_LABELS[regateProposal.entry_stage]}"* (mirrors the `coordinatorProposal` block :77-85).
- **ExchangePanel.tsx:** read `board.regate_proposal` and thread it to PipelineActionBar + WhosTurnBoard.

### §F2.5 — tests (Fix 2)
NEW (backend): `test_latest_gate_g_classifying_directive_reads_question_kind` (directive on a coordinator
`kind="question"` gate_g message IS returned; a later directive-less synthesis does NOT shadow it);
`test_infer_regate_entry_stage_design_class` (director_decision/spec_problem/route_to_designer → gate_a);
`test_infer_regate_entry_stage_code_or_none_falls_to_build` (programmer_guidance → build; NO directive → build);
`test_reset_done_tasks_for_regate` (done→todo, todo untouched, no failed left);
`test_verdict_fail_infers_build_and_resets_done` (FAIL no entry_stage, code/none → build, done tasks reset,
get_next_todo_task returns a task, is_regate, iteration+1); `test_verdict_fail_infers_gate_a_on_design_gap` (FAIL
no entry_stage, design directive → gate_a, NO task reset); `test_verdict_fail_director_override_entry_stage`
(explicit beats inference; invalid still raises OrchestratorError); `test_verdict_fail_from_pass_no_directive_infers_build`
(the PASS-then-Director-FAIL path); `test_build_regate_brief_includes_gate_g_findings` (a direct FAIL→build
re-run's cross_cutting contains the audit findings); `test_gate_a_regate_build_excludes_stale_gate_g_findings`
(after a FAIL→gate_a, a later `task_plan` message makes the old gate_g findings stale → `_latest_gate_g_findings`
returns None → the gate_a-path build brief carries NO prior findings); `test_gate_g_fail_targeted_regate_preserves_sessions`
(integration: FAIL→build, `_begin_dispatch` ran, sessions preserved). **Seed the classifying directive the PRODUCTION way** — on a
coordinator `kind="question"` message at `stage="gate_g"` with the directive in payload (NOT a `gate_report` at
`stage="build"`). NEW (FE): test_PipelineActionBar (regateProposal at gate_g/awaiting AND gate_g/blocked → FAIL→target
primary + override chips; FAIL fires `{verdict:"FAIL",entry_stage:...}`; override fires chosen stage; kickoff/release/done/gate_g
NOT offered; absent → plain FAIL `{verdict:"FAIL"}`); test_ExchangePanel (threads regate_proposal; absent → null);
test_pipeline_api (PipelineBoard tolerates absent regate_proposal).
**MUST STAY GREEN:** `test_verdict_pass_to_release` (PASS→release untouched), `test_verdict_fail_regate` (explicit
entry_stage="build" still works; its fixture has no `done` tasks so the reset is a harmless no-op),
`test_regate_preserves_agent_sessions`, `test_task_verification_loop`, `test_build_loop_runs_tasks_in_order`,
`test_end_build_advances_with_unstarted_tasks`, FE PipelineActionBar gate_g verdict block + coordinatorProposal=null +
PipelineMessageBubble synthesis/autonomous/raw-report.

---

## Implementation seams (the spec resolves these — verify the line numbers, do NOT redesign)

1. **Fix-2 classifying directive (RESOLVED in §F2.1).** Use `_latest_gate_g_classifying_directive` (stage=gate_g,
   any kind, non-null directive). Do NOT use `_latest_coordinator_directive` (gate_report-only — misses the
   gate_g `question` directive). Real refs: blocked→question :953; directive-in-payload :995-999;
   `_latest_coordinator_directive` :2021-2034.
2. **Action set (RESOLVED — no change).** `answer` is ALREADY offered at any `status=="blocked"`
   (`determine_available_actions` — note: NO leading underscore — orchestrator.py:252, :278-279). §F1.5 is a
   verify-only no-op; the only Fix-1 FE work is §F1.7.
3. **Synthesis shadowing (RESOLVED).** §F2.1's helper skips null-directive rows, so a synthesis turn (which
   carries no `coordinator_directive`, recorded by `_coordinator_synthesis` :1185-1244 with
   `extra_payload={"is_synthesis": True}`) cannot shadow the judge's directive. Add the §F2.5 test that asserts
   this.
4. **2-tuple unpack sweep (RESOLVED — exhaustive list).** `verify_done` + `_verify_with_retries` change shape.
   Update EVERY caller AND every test stub: the production caller (:1619), the `verify_done` parse-failure-note
   tests (:3301, 3327, 3346 — unpack + assert on the reason string), and the monkeypatch stub
   `_synthesis_verify_pass` (:3384 → return `(None, None)`). **Grep `monkeypatch.setattr(orchestrator,
   "verify_done"` AND `"_verify_with_retries"` and make every stub return the new tuple.** Convert the two
   mid-loop early returns at :1890 and :1892 (§F1.3c).
5. **Build→gate_g drain + re-run loop (names corrected).** The drain is `_build_open_findings`
   (orchestrator.py:1903-1920, NOT `_blocking_build_tasks_count`) + the `approve`@build `get_next_todo_task is
   None` guard (:3041-3049); `end_build` (:3257) checks only `_build_open_findings` so leftover `todo` is
   possible — `_reset_done_tasks_for_regate` handles both. The build re-run selects via `get_next_todo_task`
   (task.py:123-141); the no-todo completion branch is :2636-2642.

If any seam contradicts this spec when read against the real code, STOP and flag to Dedo — a contradiction is a
design-quality signal, never a creative patch (CLAUDE.md §2.4).

---

## Acceptance

- **Fix 1:** a mechanical verify failure auto-returns to the Auditor (unchanged, bounded by `_VERIFY_RETRIES`); a
  scope/design question escalates ONCE per iteration (status `blocked`, NO auto-return, current_actor `auditor`,
  the Director gets an answerable question + a synthesis — answerable even on a synthesis ParseFailure); a second
  scope flag in the same iteration settles to `awaiting_director` (no loop). The v1.0.1 loop cannot recur.
- **Fix 2:** a FAIL verdict re-gates to the INFERRED target — design/scope (directive present at a scope-escalation
  `blocked`) → `gate_a` (Designer fixes the spec); code-fixable or a Director-initiated FAIL on a PASS audit →
  `build` (done-tasks reset + the gate_g findings threaded into the re-run brief). The Coordinator PROPOSES it
  (board chip + FAIL→target button at gate_g/awaiting AND gate_g/blocked); the Director one-click-confirms or
  overrides to any gate_a..build stage. An explicit `entry_stage` wins; PASS→release is untouched; sessions
  preserved.
- `pytest` (full) + `vitest` + `npm run build` + `npm run lint` all green; the MUST-STAY-GREEN sets pass with
  un-weakened assertions (the parse-failure-note tests assert on the unpacked reason).

## Out of scope (deferred — flag-first if requested)

- Per-task targeted re-run on a build-class FAIL (needs the gate_g audit report to emit failing task_ids — an
  Auditor-charter change). Today: whole-build re-run with the findings threaded in.
- Finer design-gap targeting (gate_b/c/d instead of always gate_a) — needs the audit to name the offending design
  gate. Today: gate_a (Director overrides via chips).

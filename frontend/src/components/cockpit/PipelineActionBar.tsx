// Context-aware Director action buttons (F-007 §8, CR-NS-018).
//
// Buttons are derived from current_stage + status. Each primary action carries a
// muted consequence line so the outcome is unmistakable (Director feedback:
// approving past a flagged "needs fixing" was too easy). Actions needing free
// text (return/ask/answer) open an inline composer; verdict offers PASS/FAIL.

import { useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";

import type {
  CoordinatorDirective,
  PipelineActionName,
  PipelineStage,
  PipelineState,
} from "../../services/api/pipeline";
import { COORDINATOR_ACTION_LABELS, nextStageLabel, REGATE_TARGETS, STAGE_LABELS } from "./labels";

// Stages the Director ratifies with Schváliť/Vrátiť. kickoff is a ratification
// gate too — the engine's approve advances kickoff→gate_a (NOT a `start`; the
// real start is the state===null CTA on CockpitPage). gate_g (PASS/FAIL verdict)
// and gate_e (its own Customer-loop boundary actions) are excluded.
// task_plan is a plain ratify gate (CR-NS-023): approve → task_plan→build (starts the per-task
// loop); return → re-decompose (CR-NS-022 §3 resets the Designer session). build has its own block.
const RATIFY_STAGES = new Set(["kickoff", "gate_a", "gate_b", "gate_c", "gate_d", "task_plan"]);

interface Props {
  state: PipelineState | null;
  /** Backend-authoritative offerable actions (WS-C1, CR-NS-030). When present, a button renders only
   *  if its action is in this set (AND its existing finer condition holds); when absent, the FE falls
   *  back to its own hardcoded logic. Ends no-op buttons like approve on a build-blocked task. */
  availableActions?: PipelineActionName[];
  /** Build readiness (WS-C1, CR-NS-030): false → a todo task remains (final approve@build blocked).
   *  Absent → permissive (don't disable). */
  allTasksDone?: boolean;
  /** Build open findings (WS-C1, CR-NS-030): > 0 → a failed/unverified task (approve + end_build
   *  blocked). Mirrors gateEOpenFindings. */
  buildOpenFindings?: number;
  /** The latest EXECUTABLE Coordinator proposal (E7, F-008 §9) or null. Drives the build "Schváliť
   *  Koordinátorov návrh (<effect>)" button — approve → apply_coordinator_recommendation executes it. */
  coordinatorProposal?: CoordinatorDirective | null;
  /** gate_g FAIL re-gate proposal (CR-NS-057 §F2.4): the inferred target + rationale; drives the
   *  "Verdikt FAIL → <stage>" primary button + the "Iná fáza" override chips. Absent → plain "Verdikt FAIL". */
  regateProposal?: { entry_stage: PipelineStage; reason?: string } | null;
  inFlight: boolean;
  /** Blocked due to an unexpected failure (agent crash/timeout) rather than an
   *  agent question — offer "Skús znova" instead of answer/approve (CR-NS-018). */
  isErrorBlock?: boolean;
  /** A Coordinator gate_report exists to apply — gates the "Schváliť návrh
   *  Koordinátora" button (else the action would 400). CR-NS-018. */
  hasCoordinatorReport?: boolean;
  /** Gate E: the Customer signalled all 7 okruhy covered → the boundary is the
   *  FINAL sign-off (approve → Build), not a topic-continue. CR-NS-018 Phase 3. */
  gateECoverageComplete?: boolean;
  /** Gate E: count of open (unresolved) findings — any blocks closing (final /
   *  early-end). CR-NS-018 Phase 3. */
  gateEOpenFindings?: number;
  /** Gate E (revised §2): the latest milestone — a per-question stop (Designer
   *  answer) or a topic boundary (Customer gate_report). */
  gateEMode?: "question" | "boundary" | null;
  /** Gate E per-question: the Designer flagged a gap → Branch B (Opraviť/Ponechať). */
  gateEGap?: boolean;
  onAction: (action: PipelineActionName, payload?: Record<string, unknown>) => void;
}

type Composer = { action: PipelineActionName; label: string; field: string } | null;

const btn =
  "inline-flex w-fit items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium disabled:opacity-50";
const hintCls = "pl-0.5 text-[10px] leading-tight text-slate-500";

// One action = button + its consequence line, stacked.
function ActionRow({ hint, children }: { hint?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      {children}
      {hint ? <span className={hintCls}>{hint}</span> : null}
    </div>
  );
}

export function PipelineActionBar({
  state,
  availableActions,
  allTasksDone,
  buildOpenFindings,
  coordinatorProposal,
  regateProposal,
  inFlight,
  isErrorBlock = false,
  hasCoordinatorReport = false,
  gateECoverageComplete = false,
  gateEOpenFindings = 0,
  gateEMode = null,
  gateEGap = false,
  onAction,
}: Props) {
  const [composer, setComposer] = useState<Composer>(null);
  const [showRegateChips, setShowRegateChips] = useState(false); // CR-NS-057 §F2.4: "Iná fáza" override chips
  const [text, setText] = useState("");

  if (!state) return null;

  const { current_stage, status } = state;
  // WS-C1 (CR-NS-030): the backend says which actions are valid to offer. A button renders only if
  // its action is allowed (AND its existing finer condition below). Absent field → fall back to the
  // FE's own logic (allow everything), so older boards / tests keep the current behaviour.
  const allowed = (a: PipelineActionName) => (availableActions ? availableActions.includes(a) : true);
  const awaiting = status === "awaiting_director";
  const blocked = status === "blocked";
  const working = status === "agent_working";
  const paused = status === "paused";
  const isDone = status === "done";

  // Build readiness (WS-C1, CR-NS-030): the state-only available_actions OFFERS approve/end_build at a
  // settled build, but apply_action rejects them while a todo remains (approve) or a finding is open
  // (approve + end_build). Disable the buttons in those cases — like the Gate E open-finding gate —
  // instead of letting them 400. Absent fields → permissive (don't disable), for backward-compat.
  const buildHasOpenFindings = (buildOpenFindings ?? 0) > 0;
  const buildEndReady = !buildHasOpenFindings; // end_build blocks only on open findings (todos are fine)
  const buildApproveReady = allTasksDone !== false && !buildHasOpenFindings; // final sign-off: all done + clean

  // Gate E has its own boundary actions (Customer↔Designer loop) — kept out of the
  // generic ratify / question-block paths (CR-NS-018 Phase 3).
  const gateE = current_stage === "gate_e";

  // An error-block (agent crash/timeout) produced no agent output — Schváliť
  // would wrongly skip the stage and Odpoveď answers a non-question. So in that
  // case offer only "Skús znova" (re-dispatch the current stage). A question-block
  // keeps the answer/approve/return choices (CR-NS-018).
  const errorBlock = blocked && isErrorBlock;
  // CR-NS-056 §F1.7: at gate_g a blocked state is ALWAYS a Coordinator scope escalation (answerable), so
  // render "Odpoveď" even when a trailing system note (a synthesis ParseFailure) flipped isErrorBlock — else
  // the Director would be stuck on "Skús znova". The stage proxy is exact (PipelineActionBar gets only state).
  const questionBlock = blocked && !gateE && (!isErrorBlock || current_stage === "gate_g");

  // The full ratify gate (Schváliť podľa Návrhára / Koordinátora / Vrátiť) shows
  // at an awaiting ratify stage. Schváliť/Vrátiť also show on a question-block
  // (never a dead-end ask-loop) — the engine's approve/return have no status
  // guard, so they work from blocked too.
  const awaitingRatify = RATIFY_STAGES.has(current_stage) && awaiting;
  const canRatify = awaitingRatify || questionBlock;
  const gateEOpen = gateEOpenFindings > 0;
  const gateEQuestion = gateE && awaiting && gateEMode === "question";
  const gateEBoundary = gateE && awaiting && gateEMode === "boundary";

  const openComposer = (c: NonNullable<Composer>) => {
    setComposer(c);
    setText("");
  };
  const submitComposer = () => {
    if (!composer || !text.trim()) return;
    onAction(composer.action, { [composer.field]: text.trim() });
    setComposer(null);
    setText("");
  };

  if (composer) {
    return (
      <div className="flex flex-col gap-2">
        <textarea
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={composer.label}
          rows={3}
          className="w-full resize-none rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200 focus:border-primary-500 focus:outline-none"
        />
        <div className="flex items-center gap-2">
          <button
            onClick={submitComposer}
            disabled={inFlight || !text.trim()}
            className={`${btn} bg-primary-600 text-white hover:bg-primary-500`}
          >
            {inFlight ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
            {composer.label}
          </button>
          <button
            onClick={() => setComposer(null)}
            disabled={inFlight}
            className={`${btn} border border-slate-700 text-slate-400 hover:text-slate-200`}
          >
            Zrušiť
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2.5">
      {inFlight && <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-500" />}

      {/* One-click affirmative for an AGENT question with no ratify gate (CR 2026-06-12): a build
          question-block offers no "Schváliť podľa Návrhára" (approve@build = sign off the WHOLE build,
          only at awaiting — never on a mid-build question), so the Coordinator's "odporúčam schváliť"
          had no matching button and the Director had to type it via Odpoveď. Show it ONLY when answer
          is offered but approve is NOT — i.e. exactly where the ratify approve is absent — so it never
          duplicates it (at a gate question-block the approve button already covers this). */}
      {/* CR-NS-056 §F1.7: NEVER offer the rubber-stamp one-click at gate_g — a scope/design question must get
          a real typed answer (or a FAIL→target verdict), never a blind "Schvaľujem, pokračuj". */}
      {questionBlock && allowed("answer") && !allowed("approve") && current_stage !== "gate_g" && (
        <ActionRow hint="Schváliš agentov plán → pokračuje vo fáze (afirmatívna odpoveď jedným klikom).">
          <button
            onClick={() => onAction("answer", { text: "Schvaľujem, pokračuj podľa plánu." })}
            disabled={inFlight}
            className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
          >
            Schváliť a pokračovať
          </button>
        </ActionRow>
      )}

      {canRatify && (
        <>
          {allowed("approve") && (
            <ActionRow
              hint={`Prijme sa návrh Návrhára → spustí sa ďalšia fáza (${nextStageLabel(current_stage)}).`}
            >
              <button
                onClick={() => onAction("approve")}
                disabled={inFlight}
                className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
              >
                Schváliť podľa Návrhára
              </button>
            </ActionRow>
          )}

          {awaitingRatify && hasCoordinatorReport && allowed("apply_coordinator_recommendation") && (
            <ActionRow hint="Návrhárovi sa pošlú odporúčania Koordinátora na zapracovanie. Pipeline počká.">
              <button
                onClick={() => onAction("apply_coordinator_recommendation")}
                disabled={inFlight}
                className={`${btn} bg-indigo-600 text-white hover:bg-indigo-500`}
              >
                Schváliť návrh Koordinátora
              </button>
            </ActionRow>
          )}

          {allowed("return") && (
            <ActionRow hint="Napíšeš vlastnú pripomienku → Návrhár prepracuje.">
              <button
                onClick={() => openComposer({ action: "return", label: "Vrátiť s komentárom", field: "comment" })}
                disabled={inFlight}
                className={`${btn} border border-red-500/40 text-red-300 hover:bg-red-500/10`}
              >
                Vrátiť
              </button>
            </ActionRow>
          )}
        </>
      )}

      {/* Gate E per-question stop (revised §2): Branch A approve, or Branch B Opraviť/Ponechať. */}
      {gateEQuestion && !gateEGap && (
        <ActionRow hint="Odpoveď je v poriadku → Zákazník pokračuje ďalšou otázkou.">
          <button
            onClick={() => onAction("approve")}
            disabled={inFlight}
            className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
          >
            Schváliť odpoveď
          </button>
        </ActionRow>
      )}

      {gateEQuestion && gateEGap && (
        <>
          <ActionRow hint="Odporúčanie Koordinátora sa pošle cez neho Návrhárovi → opraví → ďalšia otázka.">
            <button
              onClick={() => onAction("fix")}
              disabled={inFlight}
              className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
            >
              Schváliť návrh Koordinátora
            </button>
          </ActionRow>
          <ActionRow hint="Medzera sa ponechá bez úpravy (podľa odporúčania Koordinátora) → ďalšia otázka.">
            <button
              onClick={() => onAction("leave")}
              disabled={inFlight}
              className={`${btn} border border-slate-600 text-slate-300 hover:bg-slate-800`}
            >
              Ponechať
            </button>
          </ActionRow>
        </>
      )}

      {/* Gate E topic boundary — topic-continue vs final sign-off. */}
      {gateEBoundary && !gateECoverageComplete && (
        <>
          <ActionRow hint="Okruh sa uzavrie → Zákazník pokračuje ďalším okruhom previerky.">
            <button
              onClick={() => onAction("approve")}
              disabled={inFlight}
              className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
            >
              Schváliť okruh a pokračovať
            </button>
          </ActionRow>
          <ActionRow hint="Vrátiš okruh Návrhárovi na prepracovanie.">
            <button
              onClick={() => openComposer({ action: "return", label: "Vrátiť s komentárom", field: "comment" })}
              disabled={inFlight}
              className={`${btn} border border-red-500/40 text-red-300 hover:bg-red-500/10`}
            >
              Vrátiť
            </button>
          </ActionRow>
          <ActionRow
            hint={
              gateEOpen
                ? `Najprv vyrieš otvorené nálezy (${gateEOpenFindings}) — blokujú uzavretie.`
                : `Pokrytie stačí → uzavrie Gate E a posunie na ${nextStageLabel(current_stage)}.`
            }
          >
            <button
              onClick={() => onAction("end_gate_e")}
              disabled={inFlight || gateEOpen}
              className={`${btn} border border-slate-600 text-slate-300 hover:bg-slate-800`}
            >
              Ukončiť Gate E
            </button>
          </ActionRow>
        </>
      )}

      {gateEBoundary && gateECoverageComplete && (
        <>
          <ActionRow
            hint={
              gateEOpen
                ? `Otvorené nálezy (${gateEOpenFindings}) blokujú uzavretie — najprv ich vyrieš.`
                : `Všetkých 7 okruhov pokrytých, nálezy vyriešené → posun na ${nextStageLabel(current_stage)}.`
            }
          >
            <button
              onClick={() => onAction("approve")}
              disabled={inFlight || gateEOpen}
              className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
            >
              Finálne schválenie → {nextStageLabel(current_stage)}
            </button>
          </ActionRow>
          <ActionRow hint="Vrátiš poslednému okruhu Návrhárovi na prepracovanie.">
            <button
              onClick={() => openComposer({ action: "return", label: "Vrátiť s komentárom", field: "comment" })}
              disabled={inFlight}
              className={`${btn} border border-red-500/40 text-red-300 hover:bg-red-500/10`}
            >
              Vrátiť
            </button>
          </ActionRow>
        </>
      )}

      {/* CR-NS-057 §F2.4: gate_g verdict — PASS at awaiting only; the FAIL→target group renders at gate_g
          for BOTH awaiting AND blocked (the backend allows verdict from blocked too). */}
      {current_stage === "gate_g" && allowed("verdict") && (
        <>
          {awaiting && (
            <ActionRow hint="Audit prešiel → pipeline pokračuje na vydanie.">
              <button
                onClick={() => onAction("verdict", { verdict: "PASS" })}
                disabled={inFlight}
                className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
              >
                Verdikt PASS
              </button>
            </ActionRow>
          )}
          {regateProposal && STAGE_LABELS[regateProposal.entry_stage] ? (
            <ActionRow hint={regateProposal.reason ?? "Audit neprešiel → návrat na navrhovanú fázu."}>
              <button
                onClick={() => onAction("verdict", { verdict: "FAIL", entry_stage: regateProposal.entry_stage })}
                disabled={inFlight}
                className={`${btn} bg-red-600 text-white hover:bg-red-500`}
              >
                Verdikt FAIL → {STAGE_LABELS[regateProposal.entry_stage]}
              </button>
              <button
                onClick={() => setShowRegateChips((s) => !s)}
                disabled={inFlight}
                className={`${btn} border border-slate-700 text-slate-300 hover:bg-slate-800`}
              >
                Iná fáza
              </button>
              {showRegateChips && (
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {REGATE_TARGETS.map((s) => (
                    <button
                      key={s}
                      onClick={() => onAction("verdict", { verdict: "FAIL", entry_stage: s })}
                      disabled={inFlight}
                      className={`${btn} border border-slate-700 text-slate-300 hover:bg-slate-800`}
                    >
                      {STAGE_LABELS[s]}
                    </button>
                  ))}
                </div>
              )}
            </ActionRow>
          ) : (
            <ActionRow hint="Audit neprešiel → návrat na prepracovanie.">
              <button
                onClick={() => onAction("verdict", { verdict: "FAIL" })}
                disabled={inFlight}
                className={`${btn} bg-red-600 text-white hover:bg-red-500`}
              >
                Verdikt FAIL
              </button>
            </ActionRow>
          )}
        </>
      )}

      {current_stage === "release" && awaiting && allowed("uat_accept") && (
        <ActionRow hint="Verzia sa akceptuje zákazníkom (UAT) → hotovo.">
          <button
            onClick={() => onAction("uat_accept")}
            disabled={inFlight}
            className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
          >
            Akceptovať UAT
          </button>
        </ActionRow>
      )}

      {/* Coordinator proposal (E7, F-008 §9): when the Coordinator has emitted an EXECUTABLE directive,
          the Director approves it with ONE button labelled by the concrete effect (WS-C class-D) →
          apply_coordinator_recommendation runs the matching executor. Shown at a settled build only. */}
      {current_stage === "build" &&
        (awaiting || (blocked && !isErrorBlock)) &&
        coordinatorProposal &&
        allowed("apply_coordinator_recommendation") && (
          <ActionRow hint={coordinatorProposal.rationale}>
            <button
              onClick={() => onAction("apply_coordinator_recommendation")}
              disabled={inFlight}
              className={`${btn} bg-indigo-600 text-white hover:bg-indigo-500`}
            >
              Schváliť Koordinátorov návrh (
              {COORDINATOR_ACTION_LABELS[coordinatorProposal.proposed_action] ?? coordinatorProposal.proposed_action})
            </button>
          </ActionRow>
        )}

      {/* Build per-task loop (F-007 §6/§7): the Director's controls at a build awaiting-director
          stop — sign-off, resume, rework a failed task, or early-end. The backend guards enforce
          readiness (approve blocks while todo/open findings remain; end_build blocks on a failed
          task), so the buttons are offered and the engine returns a clear error if not ready. */}
      {current_stage === "build" && awaiting && (
        <>
          {allowed("approve") && (
            <ActionRow
              hint={
                buildApproveReady
                  ? "Všetky úlohy hotové → uzavrie build a posunie na Audit."
                  : "Build ešte nie je hotový (ostávajú nepostavené alebo neoverené úlohy) — najprv ich dokonči."
              }
            >
              <button
                onClick={() => onAction("approve")}
                disabled={inFlight || !buildApproveReady}
                className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
              >
                Schváliť build → Audit
              </button>
            </ActionRow>
          )}
          {allowed("continue_build") && (
            <ActionRow hint="Prostredie opravené → pokračuj v stavaní úloh (bez komentára).">
              <button
                onClick={() => onAction("continue_build")}
                disabled={inFlight}
                className={`${btn} bg-primary-600 text-white hover:bg-primary-500`}
              >
                Pokračovať v builde
              </button>
            </ActionRow>
          )}
          {allowed("return") && (
            <ActionRow hint="Vrátiš zlyhanú úlohu (cez Koordinátora) → nový pokus s pripomienkou.">
              <button
                onClick={() => openComposer({ action: "return", label: "Vrátiť úlohu s komentárom", field: "comment" })}
                disabled={inFlight}
                className={`${btn} border border-red-500/40 text-red-300 hover:bg-red-500/10`}
              >
                Vrátiť úlohu
              </button>
            </ActionRow>
          )}
          {allowed("end_build") && (
            <ActionRow
              hint={
                buildEndReady
                  ? "Zvyšok (nepostavené úlohy) pošleš do auditu."
                  : `Otvorené úlohy (${buildOpenFindings} zlyhané/neoverené) blokujú uzavretie — najprv ich vyrieš.`
              }
            >
              <button
                onClick={() => onAction("end_build")}
                disabled={inFlight || !buildEndReady}
                className={`${btn} border border-slate-600 text-slate-300 hover:bg-slate-800`}
              >
                Ukončiť build (zvyšok do auditu)
              </button>
            </ActionRow>
          )}
          {/* accept_merged (WS-B2, CR-NS-031): a merged task dead-ended on "commit predates baseline".
              Offered only when a task actually failed (buildHasOpenFindings) — moves the baseline to the
              reported commit's parent and re-verifies. */}
          {allowed("accept_merged") && buildHasOpenFindings && (
            <ActionRow hint="Práca úlohy je v spoločnom (skoršom) commite → uzná ho a úlohu znova overí.">
              <button
                onClick={() => onAction("accept_merged")}
                disabled={inFlight}
                className={`${btn} border border-sky-500/40 text-sky-300 hover:bg-sky-500/10`}
              >
                Uznať spoločný commit
              </button>
            </ActionRow>
          )}
        </>
      )}

      {/* Build paused (CR-NS-027 + CR-NS-030): a cooperatively-paused build resumes via continue_build
          or ends early via end_build. Without this block a paused build renders no controls — the pause
          feature would be unusable from the UI (status=paused is neither awaiting nor blocked). */}
      {current_stage === "build" && paused && (
        <>
          {allowed("continue_build") && (
            <ActionRow hint="Build je pozastavený → pokračuj v stavaní úloh.">
              <button
                onClick={() => onAction("continue_build")}
                disabled={inFlight}
                className={`${btn} bg-primary-600 text-white hover:bg-primary-500`}
              >
                Pokračovať v builde
              </button>
            </ActionRow>
          )}
          {allowed("end_build") && (
            <ActionRow
              hint={
                buildEndReady
                  ? "Build ukončíš → zvyšok do auditu."
                  : `Otvorené úlohy (${buildOpenFindings} zlyhané/neoverené) blokujú uzavretie — najprv ich vyrieš.`
              }
            >
              <button
                onClick={() => onAction("end_build")}
                disabled={inFlight || !buildEndReady}
                className={`${btn} border border-slate-600 text-slate-300 hover:bg-slate-800`}
              >
                Ukončiť build (zvyšok do auditu)
              </button>
            </ActionRow>
          )}
        </>
      )}

      {questionBlock && allowed("answer") && (
        <ActionRow hint="Napíšeš agentovi vlastnú odpoveď → pokračuje vo fáze.">
          <button
            onClick={() => openComposer({ action: "answer", label: "Odpovedať agentovi", field: "text" })}
            disabled={inFlight}
            className={`${btn} bg-sky-600 text-white hover:bg-sky-500`}
          >
            Odpoveď
          </button>
        </ActionRow>
      )}

      {/* CR-NS-056 §F1.7 #1b: never offer "Skús znova" at gate_g — a scope escalation + a synthesis
          ParseFailure (trailing system note → isErrorBlock) must show ONLY the answer/verdict path, not a
          re-dispatch of the audit. (errorBlock is a separate render block from questionBlock.) */}
      {errorBlock && allowed("return") && current_stage !== "gate_g" && (
        <ActionRow hint="Znovu spustí agenta v aktuálnej fáze.">
          <button
            onClick={() => onAction("return", { comment: "Skús znova." })}
            disabled={inFlight}
            className={`${btn} bg-primary-600 text-white hover:bg-primary-500`}
          >
            Skús znova
          </button>
        </ActionRow>
      )}

      {/* Pause is build-only (CR-NS-027): only the build loop has a cooperative task boundary to
          stop at — a single-turn gate would silently complete, so we don't offer Pauza there. */}
      {working && current_stage === "build" && allowed("pause") && (
        <ActionRow hint="Pozastaví build po dokončení aktuálnej úlohy.">
          <button
            onClick={() => onAction("pause")}
            disabled={inFlight}
            className={`${btn} border border-slate-700 text-slate-300 hover:bg-slate-800`}
          >
            Pauza
          </button>
        </ActionRow>
      )}

      {!isDone && allowed("ask") && (
        // At gate_e the Director communicates only with the Coordinator (§2): the
        // input (a question OR a constatation) goes to the Coordinator, who revises
        // its recommendation. Elsewhere it stays the plain "Otázka" (its reroute is a
        // separate CR) — no lying button.
        <ActionRow
          hint={
            gateE
              ? "Otázka alebo konštatovanie → ide Koordinátorovi, ktorý prepracuje odporúčanie."
              : "Spýtaš sa, pipeline počká."
          }
        >
          <button
            onClick={() =>
              openComposer({
                action: "ask",
                label: gateE ? "Konzultovať s Koordinátorom" : "Položiť otázku",
                field: "text",
              })
            }
            disabled={inFlight}
            className={`${btn} border border-slate-700 text-slate-300 hover:bg-slate-800`}
          >
            {gateE ? "Konzultovať s Koordinátorom" : "Otázka"}
          </button>
        </ActionRow>
      )}
    </div>
  );
}

export default PipelineActionBar;

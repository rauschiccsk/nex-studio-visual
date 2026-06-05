// Context-aware Director action buttons (F-007 §8, CR-NS-018).
//
// Buttons are derived from current_stage + status. Each primary action carries a
// muted consequence line so the outcome is unmistakable (Director feedback:
// approving past a flagged "needs fixing" was too easy). Actions needing free
// text (return/ask/answer) open an inline composer; verdict offers PASS/FAIL.

import { useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";

import type {
  PipelineActionName,
  PipelineState,
} from "../../services/api/pipeline";
import { nextStageLabel } from "./labels";

// Stages the Director ratifies with Schváliť/Vrátiť. kickoff is a ratification
// gate too — the engine's approve advances kickoff→gate_a (NOT a `start`; the
// real start is the state===null CTA on CockpitPage). gate_g (PASS/FAIL verdict)
// and gate_e (its own Customer-loop boundary actions) are excluded.
const RATIFY_STAGES = new Set(["kickoff", "gate_a", "gate_b", "gate_c", "gate_d"]);

interface Props {
  state: PipelineState | null;
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
  const [text, setText] = useState("");

  if (!state) return null;

  const { current_stage, status } = state;
  const awaiting = status === "awaiting_director";
  const blocked = status === "blocked";
  const working = status === "agent_working";
  const isDone = status === "done";

  // Gate E has its own boundary actions (Customer↔Designer loop) — kept out of the
  // generic ratify / question-block paths (CR-NS-018 Phase 3).
  const gateE = current_stage === "gate_e";

  // An error-block (agent crash/timeout) produced no agent output — Schváliť
  // would wrongly skip the stage and Odpoveď answers a non-question. So in that
  // case offer only "Skús znova" (re-dispatch the current stage). A question-block
  // keeps the answer/approve/return choices (CR-NS-018).
  const errorBlock = blocked && isErrorBlock;
  const questionBlock = blocked && !isErrorBlock && !gateE;

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

      {canRatify && (
        <>
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

          {awaitingRatify && hasCoordinatorReport && (
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

          <ActionRow hint="Napíšeš vlastnú pripomienku → Návrhár prepracuje.">
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
          <ActionRow hint="Schválený návrh sa pošle cez Koordinátora Návrhárovi → opraví → ďalšia otázka.">
            <button
              onClick={() => onAction("fix")}
              disabled={inFlight}
              className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
            >
              Opraviť
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
                : "Pokrytie stačí → uzavrie Gate E a posunie na Programovanie."
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
                : "Všetkých 7 okruhov pokrytých, nálezy vyriešené → posun na Programovanie."
            }
          >
            <button
              onClick={() => onAction("approve")}
              disabled={inFlight || gateEOpen}
              className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
            >
              Finálne schválenie → Programovanie
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

      {current_stage === "gate_g" && awaiting && (
        <>
          <ActionRow hint="Audit prešiel → pipeline pokračuje na vydanie.">
            <button
              onClick={() => onAction("verdict", { verdict: "PASS" })}
              disabled={inFlight}
              className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
            >
              Verdikt PASS
            </button>
          </ActionRow>
          <ActionRow hint="Audit neprešiel → návrat na prepracovanie.">
            <button
              onClick={() => onAction("verdict", { verdict: "FAIL" })}
              disabled={inFlight}
              className={`${btn} bg-red-600 text-white hover:bg-red-500`}
            >
              Verdikt FAIL
            </button>
          </ActionRow>
        </>
      )}

      {current_stage === "release" && awaiting && (
        <ActionRow hint="Verzia sa akceptuje zákazníkom (UAT) → hotovo.">
          <button
            onClick={() => onAction("uat_accept")}
            disabled={inFlight}
            className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
          >
            UAT accept
          </button>
        </ActionRow>
      )}

      {questionBlock && (
        <ActionRow hint="Odpovieš agentovi → pokračuje vo fáze.">
          <button
            onClick={() => openComposer({ action: "answer", label: "Odpovedať agentovi", field: "text" })}
            disabled={inFlight}
            className={`${btn} bg-sky-600 text-white hover:bg-sky-500`}
          >
            Odpoveď
          </button>
        </ActionRow>
      )}

      {errorBlock && (
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

      {working && (
        <ActionRow hint="Pozastaví pipeline.">
          <button
            onClick={() => onAction("pause")}
            disabled={inFlight}
            className={`${btn} border border-slate-700 text-slate-300 hover:bg-slate-800`}
          >
            Pauza
          </button>
        </ActionRow>
      )}

      {!isDone && (
        <ActionRow hint="Spýtaš sa, pipeline počká.">
          <button
            onClick={() => openComposer({ action: "ask", label: "Položiť otázku", field: "text" })}
            disabled={inFlight}
            className={`${btn} border border-slate-700 text-slate-300 hover:bg-slate-800`}
          >
            Otázka
          </button>
        </ActionRow>
      )}
    </div>
  );
}

export default PipelineActionBar;

// Schvaľovacie body — the Manažér's approval-point buttons (CR-V2-021, design §4.4.2).
//
// Lean v2 surface (replaces the 767-LOC v1 gate-action bar). Which buttons appear is backend-authoritative:
// the board's dial-governed ``available_actions`` (orchestrator.determine_available_actions) is the single
// source of truth — the bar renders ONLY those, so it can never offer a no-op verb. Build-readiness facts
// (all_tasks_done / build_open_findings) DISABLE the Programovanie sign-off when a task is unfinished.

import { useState, type ReactNode } from "react";
import {
  ArrowRight,
  Check,
  Loader2,
  MessageCircleQuestion,
  Pause,
  Pencil,
  Play,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";

import type { BuildPhase } from "./labels";
import { nextPhaseLabel } from "./labels";
import type { PipelineActionName, PipelineState } from "../../services/api/pipeline";

interface Props {
  state: PipelineState | null;
  /** Backend-authoritative offerable actions (dial-governed); the bar renders only these. */
  availableActions?: PipelineActionName[];
  /** Programovanie sign-off gate: a todo remaining / an open finding disables ``schvalit`` at Programovanie. */
  allTasksDone?: boolean;
  buildOpenFindings?: number;
  inFlight: boolean;
  onAction: (action: PipelineActionName, payload?: Record<string, unknown>) => void;
}

export function PipelineActionBar({
  state,
  availableActions,
  allTasksDone = true,
  buildOpenFindings = 0,
  inFlight,
  onAction,
}: Props) {
  // The Manažér's free-text for uprav (rework instruction) / ask (consult) / answer (reply to a question).
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"uprav" | "ask" | "answer" | null>(null);

  if (!state) return null;

  const offered = new Set(availableActions ?? []);
  const stage = state.current_stage as BuildPhase;
  const programovanieBlocked = stage === "programovanie" && (!allTasksDone || buildOpenFindings > 0);

  const submitText = (action: "uprav" | "ask" | "answer") => {
    const value = text.trim();
    if (!value) return;
    onAction(action, action === "uprav" ? { comment: value } : { text: value });
    setText("");
    setMode(null);
  };

  // The text-entry actions (uprav / ask / answer) open an inline editor before dispatch.
  if (mode) {
    const label =
      mode === "uprav" ? "Inštrukcia na úpravu" : mode === "ask" ? "Otázka pre AI Agenta" : "Odpoveď AI Agentovi";
    return (
      <div className="flex flex-col gap-2">
        <label className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">{label}</label>
        <textarea
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          className="w-full resize-none rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-2.5 py-1.5 text-xs text-[var(--color-text-primary)] focus:border-[var(--color-accent-primary)] focus:outline-none"
          placeholder="Napíš správu…"
        />
        <div className="flex items-center gap-2">
          <button
            onClick={() => submitText(mode)}
            disabled={inFlight || !text.trim()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-50"
          >
            {inFlight ? <Loader2 className="h-3 w-3 animate-spin" /> : <ArrowRight className="h-3 w-3" />}
            Odoslať
          </button>
          <button
            onClick={() => {
              setMode(null);
              setText("");
            }}
            className="rounded-lg px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
          >
            Zrušiť
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {offered.has("start") && (
        <ActionButton onClick={() => onAction("start")} disabled={inFlight} icon={Play} primary>
          Spustiť tvorbu špecifikácie
        </ActionButton>
      )}

      {offered.has("approve_spec") && (
        <ActionButton onClick={() => onAction("approve_spec")} disabled={inFlight} icon={Check} primary>
          Schváliť špecifikáciu
        </ActionButton>
      )}

      {offered.has("schvalit") && (
        <ActionButton
          onClick={() => onAction("schvalit")}
          disabled={inFlight || programovanieBlocked}
          icon={Check}
          primary
          title={
            programovanieBlocked
              ? "Programovanie ešte nie je dokončené (zostáva úloha alebo otvorený nález)"
              : undefined
          }
        >
          Schváliť → {nextPhaseLabel(stage)}
        </ActionButton>
      )}

      {offered.has("verdict") && (
        <>
          <ActionButton
            onClick={() => onAction("verdict", { verdict: "PASS" })}
            disabled={inFlight}
            icon={ThumbsUp}
            primary
          >
            Verdikt PASS
          </ActionButton>
          <ActionButton onClick={() => onAction("verdict", { verdict: "FAIL" })} disabled={inFlight} icon={ThumbsDown}>
            Verdikt FAIL
          </ActionButton>
        </>
      )}

      {offered.has("pokracovat") && (
        <ActionButton onClick={() => onAction("pokracovat")} disabled={inFlight} icon={Play} primary>
          Pokračovať
        </ActionButton>
      )}

      {offered.has("uprav") && (
        <ActionButton onClick={() => setMode("uprav")} disabled={inFlight} icon={Pencil}>
          Uprav
        </ActionButton>
      )}

      {offered.has("answer") && (
        <ActionButton onClick={() => setMode("answer")} disabled={inFlight} icon={MessageCircleQuestion} primary>
          Odpovedať
        </ActionButton>
      )}

      {offered.has("ask") && (
        <ActionButton onClick={() => setMode("ask")} disabled={inFlight} icon={MessageCircleQuestion}>
          Spýtať sa
        </ActionButton>
      )}

      {offered.has("pause") && (
        <ActionButton onClick={() => onAction("pause")} disabled={inFlight} icon={Pause}>
          Pozastaviť
        </ActionButton>
      )}
    </div>
  );
}

interface ButtonProps {
  onClick: () => void;
  disabled?: boolean;
  primary?: boolean;
  icon: typeof Play;
  title?: string;
  children: ReactNode;
}

function ActionButton({ onClick, disabled, primary, icon: Icon, title, children }: ButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-50 ${
        primary
          ? "bg-primary-600 text-white hover:bg-primary-500"
          : "border border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]"
      }`}
    >
      <Icon className="h-3 w-3" />
      {children}
    </button>
  );
}

export default PipelineActionBar;

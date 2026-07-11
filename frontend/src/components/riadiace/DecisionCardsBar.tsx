// DecisionCardsBar — the interactive consultation surface (CR-V2-041), rebuilt for the Riadiace-centrum spine.
//
// When the build is blocked on a CONSULTATION (block_reason="decision_needed"), the AI partner has turned a
// problem (the Auditor's upfront findings; a Verifikácia-FAIL fix choice; any blocker) into a queue of
// plain-language DECISIONS. This renders them ONE CARD AT A TIME with a recommended default, so the Manažér
// (Tibor/Nazar tomorrow, Director today) resolves the build by CLICKING — never reading raw findings or writing
// a prompt. The backend already prepares the decisions (`kind=consultation` payload) and understands the
// `decide` action; this is the SCREEN half that the spine redesign (commit 7f7ba96) cut and never rebuilt — so
// every consultation had been arriving as a prose bubble with nothing to click (Director flagged live
// 2026-07-09). Acceptance criteria (design §6.1): (1) an unmissable "⛔ treba rozhodnutie" blocker banner;
// (2) plain language (the agent produced it); (3) an explicit question + what each button does; (4) it reads
// the ACTUAL blocking message (the highest-seq kind=consultation block), never a stale gate_report.
//
// Honest-by-construction (mirrors SchvalitBar): renders NOTHING unless the backend currently OFFERS `decide` in
// `board.available_actions` — which it does ONLY at `blocked` + `decision_needed`. So this bar and the other
// approval bars are mutually exclusive by construction; at most one shows.

import { useMemo, useState } from "react";
import { CircleAlert, Lightbulb } from "lucide-react";

import { postPipelineActionApi, type PipelineBoard, type PipelineMessage } from "@/services/api/pipeline";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";

interface ConsultOption {
  id: string;
  label: string;
  detail?: string;
  recommended?: boolean;
}
interface ConsultDecision {
  key: string;
  question: string;
  explanation?: string;
  options: ConsultOption[];
  rationale?: string;
  allow_free_text?: boolean;
}
interface Consultation {
  id: string;
  intro?: string;
  decisions: ConsultDecision[];
}

// The latest kind=consultation message — the ACTUAL blocking message (criterion 4) — plus its `seq`, so answers
// can be SEQ-scoped below (mirrors backend `_latest_consultation`). "Latest" = the highest seq, not the array-
// last element, so the ordering of `recent_messages` can't pick a stale consultation.
function latestConsultation(messages: PipelineMessage[]): { consultation: Consultation; seq: number } | null {
  let best: { consultation: Consultation; seq: number } | null = null;
  for (const m of messages) {
    if (!m || m.kind !== "consultation") continue;
    const c = (m.payload as { consultation?: Consultation } | null)?.consultation;
    if (c && Array.isArray(c.decisions) && c.decisions.length > 0 && (best === null || m.seq > best.seq)) {
      best = { consultation: c, seq: m.seq };
    }
  }
  return best;
}

// decision.key → the chosen label, from the durable kind=answer decide-records that belong to THIS consultation
// — i.e. with a seq strictly AFTER the consultation message. SEQ-scoped (not consultation_id-scoped) so a re-
// consultation that reuses an id or keys can NEVER fold an old answer into the new card (mirrors backend
// `_consultation_answers` — the verify-round blocker fix).
function answeredLabels(messages: PipelineMessage[], afterSeq: number): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of messages) {
    if (!m || m.kind !== "answer" || m.seq <= afterSeq) continue;
    const cd = (m.payload as { consultation_decision?: { key?: string; label?: string } } | null)
      ?.consultation_decision;
    if (cd && cd.key) out[cd.key] = cd.label ?? "—";
  }
  return out;
}

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the action returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

export default function DecisionCardsBar({ board, versionId, onBoard }: Props) {
  const messages = useMemo(() => board?.recent_messages ?? [], [board?.recent_messages]);
  const latest = useMemo(() => latestConsultation(messages), [messages]);
  const answered = useMemo(() => (latest ? answeredLabels(messages, latest.seq) : {}), [messages, latest]);

  const [picked, setPicked] = useState<string | null>(null);
  const [freeText, setFreeText] = useState("");
  const [note, setNote] = useState("");
  const [showFree, setShowFree] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  // Honest-by-construction gate: the bar exists ONLY when the backend offers `decide` right now (blocked +
  // decision_needed). Placed AFTER the hooks so the hook order is stable across renders.
  if (!board?.available_actions?.includes("decide")) return null;

  const consultation = latest?.consultation ?? null;
  if (!consultation) return null; // decide offered but the block message isn't in the recent tail — nothing to render
  const total = consultation.decisions.length;
  const idx = consultation.decisions.findIndex((d) => !(d.key in answered));
  const current = idx >= 0 ? consultation.decisions[idx] : null;
  if (!current) return null; // all decided → the apply is dispatching; the card disappears

  const canSubmit = !submitting && (showFree ? freeText.trim().length > 0 : picked != null);

  async function submit() {
    if (!current || !canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      const payload = showFree
        ? { decision_key: current.key, free_text: freeText.trim(), note: note.trim() || undefined }
        : { decision_key: current.key, option_id: picked!, note: note.trim() || undefined };
      const nextBoard = await postPipelineActionApi(versionId, { action: "decide", payload });
      onBoard(nextBoard);
      setPicked(null);
      setFreeText("");
      setNote("");
      setShowFree(false);
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Rozhodnutie zlyhalo"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="border-t border-[var(--color-border-default)] bg-[var(--color-surface)]">
      {/* (1) Unmissable blocker banner — the build is stopped and needs a decision. */}
      <div className="flex items-center gap-2 border-l-4 border-l-[var(--color-status-error)] bg-[var(--color-state-error-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--color-state-error-fg)]">
        <CircleAlert className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <span>
          ⛔ Treba tvoje rozhodnutie ({idx + 1}/{total})
        </span>
      </div>

      <div className="max-w-3xl px-4 py-3">
        {consultation.intro && (
          <p className="mb-2 text-xs text-[var(--color-text-muted)]">{consultation.intro}</p>
        )}

        {/* Answered trail — what's already decided. */}
        {Object.keys(answered).length > 0 && (
          <ul className="mb-3 space-y-0.5 text-[11px] text-[var(--color-text-muted)]">
            {consultation.decisions
              .filter((d) => d.key in answered)
              .map((d) => (
                <li key={d.key}>
                  ✓ {d.question} → <span className="text-[var(--color-text-secondary)]">{answered[d.key]}</span>
                </li>
              ))}
          </ul>
        )}

        {/* The current decision card. */}
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-4">
          <div className="mb-1 text-[10px] uppercase tracking-widest text-[var(--color-text-muted)]">
            Rozhodnutie {idx + 1} z {total}
          </div>
          <div className="text-sm font-medium text-[var(--color-text-primary)]">{current.question}</div>
          {current.explanation && (
            <p className="mt-1 text-xs text-[var(--color-text-secondary)]">{current.explanation}</p>
          )}

          {/* Options as clickable buttons; the recommended one is badged. */}
          {!showFree && (
            <div className="mt-3 space-y-2">
              {current.options.map((o) => (
                <button
                  key={o.id}
                  type="button"
                  onClick={() => setPicked(o.id)}
                  className={`block w-full rounded border px-3 py-2 text-left text-xs transition-colors ${
                    picked === o.id
                      ? "border-primary-500 bg-[var(--color-surface-hover)]"
                      : "border-[var(--color-border-default)] hover:border-primary-500"
                  }`}
                >
                  <span className="font-medium text-[var(--color-text-primary)]">{o.label}</span>
                  {o.recommended && (
                    <span className="ml-2 rounded bg-[var(--color-status-success)] px-1.5 py-0.5 text-[9px] font-semibold uppercase text-white">
                      Odporúčané
                    </span>
                  )}
                  {o.detail && <span className="mt-0.5 block text-[var(--color-text-muted)]">{o.detail}</span>}
                </button>
              ))}
            </div>
          )}

          {current.rationale && (
            <p className="mt-2 flex items-start gap-1 text-[11px] text-[var(--color-text-muted)]">
              <Lightbulb className="mt-0.5 h-3 w-3 flex-shrink-0" />
              {current.rationale}
            </p>
          )}

          {/* Free-text escape — only when the agent allowed it, never the default. */}
          {current.allow_free_text && (
            <button
              type="button"
              onClick={() => setShowFree((s) => !s)}
              className="mt-2 text-[11px] text-[var(--color-text-muted)] underline hover:text-[var(--color-text-primary)]"
            >
              {showFree ? "← Späť na možnosti" : "Iná odpoveď (napíš vlastnú)"}
            </button>
          )}
          {showFree && (
            <textarea
              lang="sk"
              value={freeText}
              onChange={(e) => setFreeText(e.target.value)}
              placeholder="Tvoja odpoveď…"
              rows={2}
              className="mt-2 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-xs text-[var(--color-text-primary)] focus:border-primary-500 focus:outline-none"
            />
          )}

          <input
            lang="sk"
            spellCheck={false}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Poznámka (nepovinné)"
            className="mt-2 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-xs text-[var(--color-text-primary)] focus:border-primary-500 focus:outline-none"
          />

          <div className="mt-3 flex items-center justify-between gap-3">
            {/* (3) Explicit "what happens" — the Manažér knows what the click does. */}
            <span className="text-[10px] text-[var(--color-text-muted)]">
              Vyber možnosť a potvrď. Keď rozhodneš všetky body, AI partner sám zapracuje zmeny a pokračuje.
            </span>
            <button
              type="button"
              onClick={submit}
              disabled={!canSubmit}
              className="shrink-0 rounded bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {submitting ? "Posielam…" : idx + 1 < total ? "Rozhodnúť → ďalšie" : "Rozhodnúť → dokončiť"}
            </button>
          </div>

          <ErrorNote error={error} className="mt-2" />
        </div>
      </div>
    </div>
  );
}

// AuditorUpfrontReview — surfaces the independent Auditor's UPFRONT review verdict + findings at the
// Návrh decision point (CR-V2-039).
//
// Without this the Manažér sees only the design doc + the Schváliť / Uprav buttons and can approve a spec
// the Auditor already flagged as holed — the WHOLE value of the upfront review (catch holes BEFORE code)
// is lost. The verdict is recorded as a ``stage=navrh, kind=verdict, payload.upfront_review=true`` message,
// but the Návrh artifact view renders the design FILE and the verdict fell through (file took precedence).
// This panel pins the verdict above the design doc so the blocking findings are unmissable.

import { useState } from "react";
import { Bell, CheckCircle2, ChevronDown, ChevronRight } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { PipelineMessage } from "../../services/api/pipeline";

interface UpfrontVerdict {
  findings: string[];
  proposed_fix?: string;
  /** Insertion order of the verdict message — so a later fail-open note can supersede a stale verdict. */
  seq: number;
}

// The latest upfront-review Auditor verdict (newest first). ``upfront_review`` marks the Návrh-phase
// independent review (vs the Verifikácia end verdict), so it shows ONLY where it belongs.
function latestUpfrontVerdict(messages: PipelineMessage[]): UpfrontVerdict | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m || m.stage !== "navrh" || m.kind !== "verdict") continue;
    const p = (m.payload as Record<string, unknown> | null) ?? {};
    if (!p.upfront_review) continue;
    return {
      findings: Array.isArray(p.findings) ? (p.findings as string[]) : [],
      proposed_fix: typeof p.proposed_fix === "string" ? p.proposed_fix : undefined,
      seq: m.seq,
    };
  }
  return null;
}

// Honest #13: the seq of the LATEST signal that the upfront review did NOT complete (fail-open). The review is
// a safety net that is treated as "no hole found" when it crashes / parse-fails — which would otherwise render
// NOTHING (a clean-looking Návrh). Detected via a payload flag (mirrors ``upfront_review_hole``) or, on the
// current backend, the ``system → manazer`` "Upfront previerka … sa nepodarila" notification. -1 = none.
function latestUpfrontFailureSeq(messages: PipelineMessage[]): number {
  let seq = -1;
  for (const m of messages) {
    if (!m || m.stage !== "navrh") continue;
    const p = m.payload as Record<string, unknown> | null;
    const flagged = !!p && (p.upfront_review_failed === true || p.upfront_review_incomplete === true);
    const noteMatch =
      m.kind === "notification" &&
      m.author === "system" &&
      typeof m.content === "string" &&
      /upfront previerka/i.test(m.content) &&
      /nepodaril/i.test(m.content);
    if ((flagged || noteMatch) && m.seq > seq) seq = m.seq;
  }
  return seq;
}

// Slovak count word: 1 nález / 2–4 nálezy / 5+ nálezov.
function findingsWord(n: number): string {
  if (n === 1) return "nález";
  if (n >= 2 && n <= 4) return "nálezy";
  return "nálezov";
}

export function AuditorUpfrontReview({ messages }: { messages: PipelineMessage[] }) {
  const verdict = latestUpfrontVerdict(messages);
  const failureSeq = latestUpfrontFailureSeq(messages);
  const [showFix, setShowFix] = useState(false);

  // Honest #13: the review FAILED / didn't complete and no LATER successful verdict superseded it → the safety
  // net didn't run. Warn ON the card so the Manažér judges the návrh themselves, instead of rendering nothing
  // (an absence that reads as a clean "bez nálezov" pass).
  const reviewDidNotRun = failureSeq >= 0 && (!verdict || failureSeq > verdict.seq);
  if (reviewDidNotRun) {
    return (
      <div className="flex-shrink-0 border-b border-l-4 border-l-[var(--color-state-warning-fg)] bg-[var(--color-state-warning-bg)] px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-[var(--color-state-warning-fg)]">
          <Bell className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
          <span>Predbežná previerka neprebehla — posúď návrh sám</span>
        </div>
        <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
          Nezávislá predbežná previerka Audítora sa nedokončila, takže táto bezpečnostná poistka teraz nebežala.
          Pred schválením si návrh a špecifikáciu prejdi pozorne sám.
        </p>
      </div>
    );
  }

  if (!verdict) return null;

  const n = verdict.findings.length;
  const hasFindings = n > 0;

  return (
    <div
      className={`flex-shrink-0 border-b border-l-4 px-4 py-3 ${
        hasFindings
          ? "border-l-[var(--color-status-error)] bg-[var(--color-state-error-bg)]"
          : "border-l-[var(--color-status-success)] bg-[var(--color-canvas)]"
      }`}
    >
      <div className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]">
        {hasFindings ? (
          <Bell className="h-4 w-4 flex-shrink-0 text-[var(--color-status-error)]" aria-hidden="true" />
        ) : (
          <CheckCircle2 className="h-4 w-4 flex-shrink-0 text-[var(--color-status-success)]" aria-hidden="true" />
        )}
        <span>
          Audítor — nezávislá predbežná previerka:{" "}
          {hasFindings
            ? `${n} ${findingsWord(n)} — vyrieš (Uprav) pred schválením`
            : "bez nálezov (v poriadku)"}
        </span>
      </div>

      {hasFindings && (
        <ul className="mt-2 max-h-48 list-disc space-y-1 overflow-y-auto pl-5 text-xs text-[var(--color-text-secondary)]">
          {verdict.findings.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      )}

      {verdict.proposed_fix && (
        <div className="mt-2 text-xs">
          <button
            type="button"
            onClick={() => setShowFix((s) => !s)}
            className="flex items-center gap-1 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
          >
            {showFix ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
            Navrhovaná oprava (Audítor)
          </button>
          {showFix && (
            <div className="prose prose-sm dark:prose-invert mt-1 max-h-48 max-w-none overflow-y-auto">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{verdict.proposed_fix}</ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default AuditorUpfrontReview;

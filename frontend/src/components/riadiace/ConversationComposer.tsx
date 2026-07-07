// ConversationComposer — the relay send box at the bottom of the Riadiace centrum (spine STEP 1). Salvaged
// from the proven (now-CUT) agent input box per the SALVAGE_VS_FRESH design choice, minus the
// break-glass `write_rejected` wiring — the spine page has no raw-PTY keystroke path (the break-glass PTY is
// dormant), so the sole channel is the engine relay.
//
// The Manažér's message is RELAYED through the engine (POST /pipeline/{version}/relay) as the AI Agent's next
// `--resume` turn — SPIKE-IO Model B: the engine is the SOLE writer to the warm `claude` session. When a turn
// is in flight the relay returns `deferred: true` (the message is enqueued behind the in-flight turn and lands
// at the next turn boundary) — we surface the design-mandated busy hint.

import { useState, type FormEvent, type KeyboardEvent } from "react";
import { Loader2, Send } from "lucide-react";

const ENGINE_BUSY_HINT = "Engine práve pracuje — správa sa pošle po dokončení ťahu.";
// Director observation #6: a framework_issue block is an agent → Dedo escalation. The Manažér cannot fix a
// NEX Studio bug via Uprav — the composer is locked with this banner until Dedo clears the block.
const FRAMEWORK_ISSUE_BANNER =
  "Toto musí opraviť Dedo — už dostal správu. Manažér to nevie cez Uprav. Počkaj na Deda.";

interface Props {
  /** Relay the text through the engine; resolves to whether it was ENQUEUED behind an in-flight turn. */
  onRelay: (text: string) => Promise<{ deferred: boolean }>;
  disabled?: boolean;
  /** Director obs #6: the build is blocked on a framework_issue (escalated to Dedo) — lock the composer +
   *  show the "wait for Dedo" banner; the Manažér has no move here (only Dedo clears it). */
  frameworkBlocked?: boolean;
}

export function ConversationComposer({ onRelay, disabled, frameworkBlocked }: Props) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A framework_issue escalation hard-disables the whole composer (Manažér has no recovery move).
  const locked = disabled || frameworkBlocked;

  async function submit() {
    const trimmed = text.trim();
    if (!trimmed || sending || locked) return;
    setSending(true);
    setError(null);
    setHint(null);
    try {
      const { deferred } = await onRelay(trimmed);
      setText("");
      // `deferred` ⇒ a turn was in flight; the message is queued and lands at the next boundary.
      setHint(deferred ? ENGINE_BUSY_HINT : null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Správu sa nepodarilo odoslať.");
    } finally {
      setSending(false);
    }
  }

  function onFormSubmit(e: FormEvent) {
    e.preventDefault();
    void submit();
  }

  // Enter sends; Shift+Enter inserts a newline (standard chat ergonomics).
  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  }

  return (
    <form
      onSubmit={onFormSubmit}
      className="flex-shrink-0 border-t border-[var(--color-border-default)] bg-[var(--color-surface)] p-3"
    >
      {frameworkBlocked && (
        <div
          role="alert"
          className="mb-2 rounded border border-[var(--color-state-error-fg)]/30 bg-[var(--color-state-error-bg)] px-2 py-1.5 text-[11px] font-medium text-[var(--color-state-error-fg)]"
        >
          {FRAMEWORK_ISSUE_BANNER}
        </div>
      )}
      {(hint || error) && (
        <div
          className={`mb-2 rounded px-2 py-1 text-[11px] ${
            error
              ? "bg-[var(--color-state-error-bg)] text-[var(--color-state-error-fg)]"
              : "bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]"
          }`}
        >
          {error ?? hint}
        </div>
      )}
      <div className="flex items-end gap-2">
        <textarea
          lang="sk"
          spellCheck={false}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={locked || sending}
          rows={2}
          placeholder={
            frameworkBlocked
              ? "Zablokované — čaká sa na opravu NEX Studia (Dedo)."
              : "Napíš AI Agentovi… (Enter odošle, Shift+Enter nový riadok)"
          }
          className="min-h-[2.5rem] flex-1 resize-none rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent-primary)] focus:outline-none disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={locked || sending || !text.trim()}
          className="flex h-9 items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-40"
        >
          {sending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
          Poslať
        </button>
      </div>
    </form>
  );
}

export default ConversationComposer;

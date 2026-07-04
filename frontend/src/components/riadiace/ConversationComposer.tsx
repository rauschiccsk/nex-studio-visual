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

interface Props {
  /** Relay the text through the engine; resolves to whether it was ENQUEUED behind an in-flight turn. */
  onRelay: (text: string) => Promise<{ deferred: boolean }>;
  disabled?: boolean;
}

export function ConversationComposer({ onRelay, disabled }: Props) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const trimmed = text.trim();
    if (!trimmed || sending || disabled) return;
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
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={disabled || sending}
          rows={2}
          placeholder="Napíš AI Agentovi… (Enter odošle, Shift+Enter nový riadok)"
          className="min-h-[2.5rem] flex-1 resize-none rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent-primary)] focus:outline-none disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={disabled || sending || !text.trim()}
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

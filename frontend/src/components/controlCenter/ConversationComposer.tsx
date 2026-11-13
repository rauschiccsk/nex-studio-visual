// ConversationComposer — the relay send box at the bottom of the Riadiace centrum (spine STEP 1). Salvaged
// from the proven (now-CUT) agent input box per the SALVAGE_VS_FRESH design choice, minus the
// break-glass `write_rejected` wiring — the spine page has no raw-PTY keystroke path (the break-glass PTY is
// dormant), so the sole channel is the engine relay.
//
// The Manažér's message is RELAYED through the engine (POST /pipeline/{version}/relay) as the AI Agent's next
// `--resume` turn — SPIKE-IO Model B: the engine is the SOLE writer to the warm `claude` session. When a turn
// is in flight the relay returns `deferred: true` (the message is enqueued behind the in-flight turn and lands
// at the next turn boundary) — we surface the design-mandated busy hint.

import { useEffect, useState, type FormEvent, type KeyboardEvent } from "react";
import { Loader2, RotateCcw, Send } from "lucide-react";

import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import { RESTORED_DRAFT_LABEL, useDraft, draftKey } from "@/hooks/useDraft";
import { usePoradcaHandoff } from "@/hooks/usePoradcaHandoff";
import { FROM_PORADCA_LABEL, draftCameFromPoradca, handOffInstruction } from "@/lib/poradcaHandoff";
import type { InputOwner } from "@/components/controlCenter/blockRecovery";
import AttachFileButton from "@/components/controlCenter/AttachFileButton";
import { withAttachedLine } from "@/components/controlCenter/privateFiles";
import { humanizeApiError, type HumanError } from "@/services/apiError";

const ENGINE_BUSY_HINT = "Staviteľ práve pracuje — správa sa pošle, keď dokončí.";
// A framework_issue block means the bug is in NEX Studio itself — the Manažér cannot chat their way out of a
// NEX-Studio bug (correct to keep the composer locked). Plain Slovak, no "Dedo"/"framework" jargon: our
// technical team resolves it.
const FRAMEWORK_ISSUE_BANNER = "Túto chybu rieši náš technický tím.";
// Category I: when a recovery bar above owns the input (a blocked question / error), the always-open composer
// COLLAPSES to this slim pointer (nex-studio-visual crash-test 2026-07-13 — a second live textarea read as a
// confusing duplicate input, "2 editory"). Neutral wording: a question is answered, an error is retried, both
// "v lište vyššie".
const BLOCKED_ABOVE_HINT = "Pokračuj cez lištu vyššie.";
// DEV-27: during a consultation the decisions are made on the Decision Card; a message here is a QUESTION
// about it — the engine answers it aside and the documents stay as they are.
const CONSULTATION_HINT =
  "Prebieha konzultácia — rozhoduj na karte vyššie. Tu sa môžeš AI partnera len opýtať; dokumenty sa tým nezmenia.";

/** DEV-297: where the box also carries „Uprav“, the two buttons do different things — say which is which.
 * @internal — exported for its test. */
export const UPRAV_HINT =
  "„Poslať“ pošle text Staviteľovi ako správu. „Uprav“ mu vráti prácu na úpravu podľa tohto textu — pri pláne úloh plán.";

// DEV-298: Poradca's instruction is work, never a question — while the box holds it, „Uprav“ is the main button and
// Enter sends it so. Remembered per build until the text goes out or the box is emptied, edits and reloads included.
/** DEV-298: the line while the box holds Poradca's instruction — which button sends it.
 * @internal — exported for its test. */
export const UPRAV_INSTRUCTION_HINT =
  "Pokyn od Poradcu je pokyn na prácu — odošli ho tlačidlom „Uprav“ (aj Enter). „Poslať“ by ho poslal len ako správu.";

const MAIN_BUTTON =
  "flex h-9 items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-40";
const OTHER_BUTTON =
  "flex h-9 items-center gap-1.5 rounded-lg border border-[var(--color-border-strong)] px-3 text-xs font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-40";

function upravIntentKey(versionId: string | null | undefined): string | null {
  const key = draftKey("rozhovor", versionId);
  return key ? `${key}.uprav` : null;
}

function readUpravIntent(versionId: string | null | undefined): boolean {
  const key = upravIntentKey(versionId);
  try {
    return !!key && window.localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function writeUpravIntent(versionId: string | null | undefined, on: boolean): void {
  const key = upravIntentKey(versionId);
  if (!key) return;
  try {
    if (on) window.localStorage.setItem(key, "1");
    else window.localStorage.removeItem(key);
  } catch {
    // The browser refuses storage: the main button still follows the instruction while the page stays open.
  }
}

interface Props {
  /** Relay the text through the engine; resolves to whether it was ENQUEUED behind an in-flight turn. */
  onRelay: (text: string) => Promise<{ deferred: boolean }>;
  disabled?: boolean;
  /** The build is blocked on a framework_issue (a NEX-Studio bug) — lock the composer + show the "technical
   *  team is on it" banner; the Manažér cannot chat their way out of it (ReportAgainBar owns the one move). */
  frameworkBlocked?: boolean;
  /** Category I: a recovery bar above (a blocked question / error) owns the input right now. The always-open
   *  composer stays usable but is de-emphasised, with a hint pointing at the one obvious input above. */
  blockedAbove?: boolean;
  /** At the Vizuál gate this composer IS the change-request channel (typed → the AI applies live, HMR), so
   *  the placeholder names that explicitly ("Napíš požiadavku na zmenu vizuálu…"). Director 2026-07-13. */
  atVizual?: boolean;
  /** ICCINT-30: scopes the saved draft to THIS build, so two half-written messages never mix. */
  versionId?: string | null;
  /** DEV-22: the build state is known (the board has loaded). Until then nobody can tell whether this box or
   *  the recovery bar above takes the Manažér's text, so a pending Poradca instruction must wait. */
  inputReady?: boolean;
  /** DEV-26: which box takes the Manažér's text in this state (`inputOwner`). Only when it is this one does a
   *  Poradca instruction land here; otherwise one parked here moves on to the box that does. */
  owner?: InputOwner | null;
  /** DEV-297: the board offers „Uprav“ and no other control sends it (`composerOffersRevise`) — this box carries it. */
  offersUprav?: boolean;
  /** Send the text as „Uprav“ (``uprav`` with the text as its comment). */
  onUprav?: (comment: string) => Promise<void>;
  /** DEV-373: the button's name as the cockpit names it for this state (the board's ``action_labels.uprav``) — the
   *  page passes it with ``offersUprav``; the box has no word of its own for it. */
  upravLabel?: string;
  /** DEV-351: the version has not started — the box is locked until „Spustiť tvorbu špecifikácie“ above is pressed;
   *  a message never starts it (it used to start it as a conversation, without the five phases). */
  notStarted?: boolean;
}

function ConversationComposer({
  onRelay,
  disabled,
  frameworkBlocked,
  blockedAbove,
  atVizual,
  versionId,
  inputReady = true,
  owner = "rozhovor",
  offersUprav = false,
  onUprav,
  upravLabel,
  notStarted = false,
}: Props) {
  // ICCINT-30: the box the Director lost a message from. Persisted per build, cleared on a real send.
  const { text, setText, clear: clearDraft, restored } = useDraft(draftKey("rozhovor", versionId));
  const [sending, setSending] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const [error, setError] = useState<HumanError | null>(null);
  // Same growth rule as the recovery bar — the two boxes the Manažér writes to must behave alike.
  const growRef = useAutoGrowTextarea(text);

  // A framework_issue escalation hard-disables the whole composer (Manažér has no recovery move here).
  const locked = disabled || frameworkBlocked || notStarted;
  // Category I: a recovery bar above owns the input — COLLAPSE the composer to a pointer (framework_issue keeps
  // its own locked banner below, so it is excluded here).
  const collapsed = !!blockedAbove && !frameworkBlocked;
  const takesInstruction = owner === "rozhovor";
  // ICCINT-167 / DEV-22: an instruction from Poradca lands here only while THIS box takes the text.
  const { fromPoradca, dismiss: dismissPoradca } = usePoradcaHandoff({
    surface: "rozhovor",
    versionId,
    live: inputReady && takesInstruction && !collapsed && !locked && !!versionId,
    text,
    setText,
    restored,
  });

  // DEV-298: the box holds Poradca's instruction — it stays his instruction when he edits it, until it goes out.
  const [upravIntent, setUpravIntent] = useState<boolean>(() => readUpravIntent(versionId));
  useEffect(() => {
    setUpravIntent(readUpravIntent(versionId));
  }, [versionId]);
  useEffect(() => {
    const on = fromPoradca ? true : text.trim() ? upravIntent : false;
    if (on === upravIntent) return;
    setUpravIntent(on);
    writeUpravIntent(versionId, on);
  }, [fromPoradca, text, upravIntent, versionId]);

  // DEV-22 / DEV-26: Poradca's instruction parked here (handed off while the agent was working) must not stay
  // in a box that no longer takes the text — the build blocked on a question, or a consultation opened. Hand it
  // on to the box that took the input over.
  useEffect(() => {
    if (!inputReady || (takesInstruction && !collapsed) || !versionId || !text.trim()) return;
    if (!draftCameFromPoradca(versionId, "rozhovor")) return;
    handOffInstruction(versionId, text);
    clearDraft();
    dismissPoradca();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs when the owner changes, not on every keystroke
  }, [inputReady, takesInstruction, collapsed, versionId]);

  async function submit() {
    const trimmed = text.trim();
    if (!trimmed || sending || locked) return;
    setSending(true);
    setError(null);
    setHint(null);
    try {
      const { deferred } = await onRelay(trimmed);
      clearDraft();
      dismissPoradca();
      // `deferred` ⇒ a turn was in flight; the message is queued and lands at the next boundary.
      setHint(deferred ? ENGINE_BUSY_HINT : null);
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Odoslanie správy zlyhalo"));
    } finally {
      setSending(false);
    }
  }

  // DEV-297: the same text, sent as „Uprav“ — the work goes back to the agent with it as the comment.
  async function uprav() {
    const trimmed = text.trim();
    if (!trimmed || sending || locked || !onUprav) return;
    setSending(true);
    setError(null);
    setHint(null);
    try {
      await onUprav(trimmed);
      clearDraft();
      dismissPoradca();
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Úprava zlyhala"));
    } finally {
      setSending(false);
    }
  }
  const showUprav = offersUprav && !!onUprav;
  const primaryUprav = showUprav && upravIntent;

  function onFormSubmit(e: FormEvent) {
    e.preventDefault();
    void submit();
  }

  // Enter sends; Shift+Enter inserts a newline (standard chat ergonomics).
  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void (primaryUprav ? uprav() : submit());
    }
  }

  // Category I: a recovery bar above owns the input — render only a slim pointer, so there is exactly ONE live
  // input on screen (the bar above). No second textarea to read as a duplicate.
  if (collapsed) {
    return (
      <div className="flex-shrink-0 border-t border-[var(--color-border-default)] bg-[var(--color-surface)] px-3 py-2">
        <p className="text-[11px] font-medium text-[var(--color-text-muted)]">{BLOCKED_ABOVE_HINT}</p>
      </div>
    );
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
      {owner === "karta" && (
        <p className="mb-2 text-[11px] font-medium text-[var(--color-text-muted)]">{CONSULTATION_HINT}</p>
      )}
      {(hint || error) && (
        <div
          className={`mb-2 rounded px-2 py-1 text-[11px] ${
            error
              ? "bg-[var(--color-state-error-bg)] text-[var(--color-state-error-fg)]"
              : "bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]"
          }`}
        >
          {error ? error.message : hint}
        </div>
      )}
      {/* DEV-222: the line about where the text came from stands ABOVE the row — beside the field it took the width
          of its sentence and left the field a third of the panel (Author Asistent, 24.10.2026). */}
      {restored && !fromPoradca && (
        // ICCINT-30: text that appears by itself must be recognisable as HIS earlier draft — not as
        // something someone else wrote into his box while he was away.
        <p className="mb-1 text-[11px] text-[var(--color-text-muted)]">{RESTORED_DRAFT_LABEL}</p>
      )}
      {fromPoradca && (
        <p className="mb-1 text-[11px] font-medium text-[var(--color-accent-primary)]">{FROM_PORADCA_LABEL}</p>
      )}
      {showUprav && (
        <p className="mb-1 text-[11px] text-[var(--color-text-muted)]">
          {primaryUprav ? UPRAV_INSTRUCTION_HINT : UPRAV_HINT}
        </p>
      )}
      <div className="flex items-end gap-2">
        <textarea
          data-draft="rozhovor"
          lang="sk"
          spellCheck={true}
          ref={growRef}
          value={text}
          onChange={(e) => {
            dismissPoradca();
            setText(e.target.value);
          }}
          onKeyDown={onKeyDown}
          disabled={locked || sending}
          rows={1}
          placeholder={
            frameworkBlocked
              ? "Zablokované — túto chybu rieši náš technický tím."
              : notStarted
                ? "Verzia ešte nebeží — spusti tvorbu špecifikácie tlačidlom vyššie."
                : atVizual
                  ? "Napíš požiadavku na zmenu vizuálu… (Enter odošle, Shift+Enter nový riadok)"
                  : "Napíš Staviteľovi… (Enter odošle, Shift+Enter nový riadok)"
          }
          className="min-h-[2.5rem] flex-1 resize-none rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent-primary)] focus:outline-none disabled:opacity-50"
        />
        {versionId && (
          // DEV-44: a file for the agent outside a question too — its path lands in this message.
          <AttachFileButton
            versionId={versionId}
            disabled={locked || sending}
            onAttached={(line) => {
              dismissPoradca();
              setText(withAttachedLine(text, line));
            }}
          />
        )}
        <button
          type="submit"
          disabled={locked || sending || !text.trim()}
          data-primary={primaryUprav ? "false" : "true"}
          className={primaryUprav ? OTHER_BUTTON : MAIN_BUTTON}
        >
          {sending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
          Poslať
        </button>
        {showUprav && (
          <button
            type="button"
            onClick={() => void uprav()}
            disabled={locked || sending || !text.trim()}
            title="Vráti prácu Staviteľovi na úpravu podľa tohto textu"
            data-primary={primaryUprav ? "true" : "false"}
            className={primaryUprav ? MAIN_BUTTON : OTHER_BUTTON}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            {upravLabel}
          </button>
        )}
      </div>
    </form>
  );
}

export default ConversationComposer;

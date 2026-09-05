// DedoProposalBar — a finding from our technical team, one click away from the agent (ICCINT-24).
//
// The thing this replaces was a person. The Director would ask Dedo to review the AI Agent's work, Dedo
// would measure and find real mistakes — and the only way those mistakes reached the agent was the Director
// reading Dedo's text and RETYPING it into the box below. Four times in one day. The return leg built in
// ICCINT-12/13/14 did not help: it was built for the case where the agent escalates a bug in NEX Studio
// itself, which has not happened once in the product's life.
//
// What does NOT change is who decides. Dedo may not write into a healthy build's agent (ICCINT-14 §4.5: his
// message is not a comment, it is the directive that opens the agent's next prompt — unchecked, he would
// quietly be steering every customer's build). So his finding arrives here as a PROPOSAL: recorded,
// delivered to nobody, waiting. The Manažér reads it, edits it if he wants, and presses send — or declines
// it. "The Manažér decides" and "the Manažér transcribes" are not the same thing; only the second one goes
// away.
//
// The send is not a new road to the agent. It fires the ORDINARY verb the proposal names (uprav / answer /
// ask), the same one the Manažér would have clicked himself, with all of that verb's guards — so an
// "answer" on a build that is not asking anything is refused exactly as it always was, the proposal stays
// open, and the bar is still here when the state allows it.
//
// Honest-by-construction, like every riadiace bar: renders NOTHING unless the backend says a proposal is
// open (`board.dedo_proposal`). Once sent or declined the row is archived and the field goes null — so it
// is never offered twice, and never offered at all when there is nothing to offer.
//
// WHAT HE SEES IS WHAT HE SENDS. Both buttons carry the id of the proposal on screen, so the server acts on
// THAT finding and reads the verb off it. Before (audit 2026-08-23) the server looked the open proposal up
// again when the click arrived — and this screen reconciles every 25s while Dedo re-measures constantly, so
// a finding written in between was the one that ran: the button said "Spýtať sa agenta" and the engine got
// "uprav" (work handed back, tasks reset, the loop re-dispatched). If the named finding is no longer open
// the server refuses (409) instead of substituting; this bar then pulls the fresh board and says why, so he
// is never left deciding about something that is no longer on the desk.

import { useDraft, draftKey } from "@/hooks/useDraft";
import { useEffect, useState } from "react";
import { Lightbulb, Send, X } from "lucide-react";

import { useOpenVersionCockpit } from "@/hooks/useOpenVersionCockpit";
import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";

import {
  sendDedoProposalApi,
  rejectDedoProposalApi,
  getPipelineBoardApi,
  type PipelineBoard,
} from "@/services/api/pipeline";
import { ApiError } from "@/services/api";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";
// The amber chrome only — NOT WarningActionBar itself: that shape is title + one sentence + ONE button, and
// this bar is an editable box with two (send / decline). Sharing the tokens is DRY; sharing a shape that
// does not fit would be abstraction for its own sake (the note that component carries about itself).
import { WARNING_CHROME } from "@/components/common/WarningActionBar";

// What the button does, said as the consequence rather than as the engine's verb. The Manažér never picks
// an action — Dedo's proposal carries it and this only tells him, in his own words, what pressing it means.
const ACTION_COPY: Record<string, { button: string; effect: string }> = {
  uprav: {
    button: "Vrátiť agentovi na doplnenie",
    effect:
      "AI Agent dostane tento text ako pokyn a vráti sa k rozrobenej práci — nič sa nezačína odznova.",
  },
  answer: {
    button: "Odpovedať agentovi",
    effect: "Text sa odošle ako odpoveď na otázku, na ktorej sa AI Agent zastavil, a on pokračuje ďalej.",
  },
  ask: {
    button: "Spýtať sa agenta",
    effect: "Text sa odošle AI Agentovi ako otázka a on na ňu v ďalšom ťahu odpovie.",
  },
  // ICCINT-54: jediné sloveso, ktoré nepokračuje v TEJTO stavbe, ale zakladá novú záplatovú verziu.
  // Text to hovorí nahlas — Manažér musí vedieť, že klikom nevzniká správa, ale nová stavba.
  fast_fix: {
    button: "Spustiť rýchlu opravu",
    effect:
      "Vznikne nová záplatová verzia a rovno sa na nej rozbehne rýchla oprava s týmto zadaním — " +
      "krátkou cestou bez Vizuálu. Nasadenie sa nespustí; to zostáva samostatným krokom.",
  },
  // ICCINT-56: jediné sloveso mierené na KARTU ROZHODNUTIA, nie na agenta priamo. Keď je stavba zaseknutá
  // na karte, kokpit iné slovesá neponúka — a po piatich neúspešných kolách engine zámerne odoberá aj
  // jednoklikovú voľbu, takže klávesnica ostávala jedinou cestou vpred. Toto je tá cesta bez písania.
  decide: {
    button: "Odpovedať na kartu rozhodnutia",
    effect:
      "Text sa odošle ako tvoja vlastná odpoveď na otvorenú otázku — AI Agent ju dostane ako cielenú " +
      "opravu a Auditor ju znova overí. Karta sa tým vyrieši.",
  },
};

const FALLBACK_COPY = {
  button: "Odoslať agentovi",
  effect: "Text sa odošle AI Agentovi.",
};

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the call returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

export default function DedoProposalBar({ board, versionId, onBoard }: Props) {
  const openVersionCockpit = useOpenVersionCockpit();
  const proposal = board?.dedo_proposal ?? null;
  const proposalId = proposal?.message_id ?? null;

  // ICCINT-30: his EDITS to the proposal survive leaving the screen. Without this he could return to the
  // untouched original and send a version he had already decided against, believing it was his.
  const draft = useDraft(draftKey(`navrh.${proposal?.message_id ?? "none"}`, versionId));
  const text = draft.text || (proposal?.content ?? "");
  const setText = draft.setText;
  const [busy, setBusy] = useState<"send" | "reject" | null>(null);
  const [error, setError] = useState<HumanError | null>(null);
  // What the server said when it refused BECAUSE the finding had changed under him (409). Kept apart from
  // `error` on purpose: the refusal is followed by a board refresh, which swaps the proposal — and the
  // effect below clears `error` on a new proposal id, so an ordinary error state would vanish in the same
  // tick as the explanation for why the screen just changed. This one survives the swap and is cleared
  // when he acts again.
  const [staleNotice, setStaleNotice] = useState<string | null>(null);
  // Hooks run BEFORE the honest-by-construction early return below — a hook after a conditional return is
  // a hook that sometimes does not run, which React forbids.
  const growRef = useAutoGrowTextarea(text);

  // Adopt the wording of whichever proposal is open. Keyed on the message id, not the text: while the
  // Manažér is editing, a board refresh (the 25s reconcile) re-delivers the SAME proposal and must not
  // wipe what he has typed. A genuinely NEW finding is a different id and does replace the box.
  useEffect(() => {
    if (proposalId) {
      draft.clear();
      setError(null);
    }
  }, [proposalId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!proposal) return null;

  // The id travels with both buttons. Bound HERE, after the early return, so it is the id of the proposal
  // actually on screen at the moment of the click — that binding IS the guarantee (see the header note).
  const decidingAbout = proposal.message_id;
  const copy = ACTION_COPY[proposal.proposed_action] ?? FALLBACK_COPY;
  const edited = text.trim() !== proposal.content.trim();
  const canSend = busy === null && text.trim().length > 0;

  // A 409 means exactly one thing here: the finding on this screen is not the one on the desk any more —
  // it was already sent, already declined, or Dedo replaced it while the Manažér was reading. The server
  // refuses rather than acting on the newer one (that substitution is the defect this whole path was
  // rebuilt to remove), so the cockpit's job is to STOP SHOWING HIM THE STALE ONE. Pull the fresh board and
  // keep the server's sentence on screen, so the swap he is about to see has a reason next to it. Telling
  // him "refresh the page" instead would be the app handing its own work back to the manager.
  async function handleFailure(err: unknown, phrase: string): Promise<void> {
    if (err instanceof ApiError && err.status === 409) {
      setStaleNotice(err.message || "Návrh sa medzitým zmenil.");
      setError(null);
      try {
        onBoard(await getPipelineBoardApi(versionId));
      } catch {
        // The refresh is the courtesy, not the guarantee — the 25s reconcile will do it anyway. Never
        // let a failed refresh swallow the explanation.
      }
      // (When the refreshed board carries NO proposal — the desk really is empty, e.g. it was declined in
      // another tab — the bar unmounts and takes the notice with it. That is the honest end state: there is
      // nothing left to decide about, and a bar with no proposal would be the dishonest alternative.)
      return;
    }
    setError(humanizeApiError(err, phrase));
  }

  async function send() {
    if (!canSend) return;
    setError(null);
    setStaleNotice(null);
    setBusy("send");
    try {
      const next = await sendDedoProposalApi(versionId, decidingAbout, text.trim());
      onBoard(next);
      // ICCINT-62: `fast_fix` is the one verb that STARTS A NEW VERSION rather than continuing this one, so
      // the response describes a different build. Without this the bar vanished and the Manažér kept staring
      // at the old version, certain nothing had happened — while the agent was already working.
      const started = next.state?.version_id;
      if (proposal?.proposed_action === "fast_fix" && started && started !== versionId) {
        await openVersionCockpit(started);
      }
    } catch (err: unknown) {
      await handleFailure(err, "Odoslanie návrhu zlyhalo");
    } finally {
      setBusy(null);
    }
  }

  async function reject() {
    if (busy !== null) return;
    setError(null);
    setStaleNotice(null);
    setBusy("reject");
    try {
      onBoard(await rejectDedoProposalApi(versionId, decidingAbout));
    } catch (err: unknown) {
      await handleFailure(err, "Odmietnutie návrhu zlyhalo");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="border-t border-[var(--color-border-default)] bg-[var(--color-surface)]">
      {/* WHO this is from — said first, because the text below is not the Manažér's own and he must never
          send it thinking it was. Amber: something for him to weigh, not an error and not a green light. */}
      <div
        className={`flex items-center gap-2 ${WARNING_CHROME} px-4 py-2.5 text-sm font-semibold text-[var(--color-state-warning-fg)]`}
      >
        <Lightbulb className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <span>Dedo (náš technický tím) našiel niečo, čo navrhuje poslať AI Agentovi</span>
      </div>

      <div className="flex flex-col gap-2 px-4 py-3">
        {/* The finding he was about to act on had changed under him — said BEFORE the new text, because it
            explains why the box in front of him is not the one he was reading a moment ago. */}
        {staleNotice && (
          <p
            role="status"
            className="rounded-md border-l-2 border-[var(--color-state-warning-fg)] bg-[var(--color-state-warning-bg)] px-3 py-2 text-xs text-[var(--color-text-secondary)]"
          >
            {staleNotice}
          </p>
        )}

        <p className="text-xs text-[var(--color-text-muted)]">
          Text napísal Dedo — agentovi zatiaľ NEODIŠIEL a agent o ňom nevie. Môžeš ho upraviť alebo doplniť;
          odoslať ho môžeš len ty. {copy.effect}
        </p>

        <textarea
          lang="sk"
          spellCheck={true}
          ref={growRef}
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy !== null}
          aria-label="Návrh od Deda — text pre AI Agenta"
          className="w-full resize-none rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-canvas)] px-3 py-1.5 text-xs text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:border-primary-500 focus:outline-none disabled:opacity-60"
        />

        {/* An edit is worth saying out loud: what goes into the record is what the Manažér sends, and it is
            his message from that moment on. Dedo's original stays in the protokol either way. */}
        {edited && (
          <p className="text-xs text-[var(--color-text-muted)]">
            Text si upravil — odošle sa tvoje znenie. Pôvodný Dedov text zostáva zapísaný v protokole.
          </p>
        )}

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={reject}
            disabled={busy !== null}
            className="flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
            {busy === "reject" ? "Odmietam…" : "Odmietnuť návrh"}
          </button>
          <button
            type="button"
            onClick={send}
            disabled={!canSend}
            className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Send className="h-3.5 w-3.5" aria-hidden="true" />
            {busy === "send" ? "Odosielam…" : copy.button}
          </button>
        </div>

        <ErrorNote error={error} />
      </div>
    </div>
  );
}

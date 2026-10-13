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

import {
  postPipelineActionApi,
  type DecisionCardContext,
  type PipelineBoard,
  type PipelineMessage,
} from "@/services/api/pipeline";
import { inputOwner } from "@/components/riadiace/blockRecovery";
import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import { RESTORED_DRAFT_LABEL, draftKey, useDraft } from "@/hooks/useDraft";
import { usePoradcaHandoff } from "@/hooks/usePoradcaHandoff";
import { FROM_PORADCA_LABEL } from "@/lib/poradcaHandoff";
import { smerDoRiadku, ziskKola } from "@/lib/smerKonzultacie";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";
import ReviewWithPoradcaButton from "@/components/riadiace/ReviewWithPoradcaButton";
import { reviewRequestFor } from "@/lib/poradcaReview";

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
  /**
   * ICCINT-122/123 — odkiaľ otázka prišla. Karta ukázala otázku a možnosti, ale nie to, PREČO
   * vznikla. Manažér nevie rozlíšiť, či je to dôsledok jeho vlastného rozhodnutia, alebo nález, čo
   * tam ležal od začiatku; vidí len, že otázok pribudlo — a to si prirodzene vysvetlí ako rozpad.
   * Chýba na starých kartách; vtedy sa žiadna veta nevymýšľa.
   */
  origin?: "objav" | "dosledok" | "odklad";
  /** Pri `dosledok`: čoho je to dôsledok. */
  origin_of?: string;
  /** v4.0.11: the jargon backing `explanation`, shown collapsed behind a "Technický detail" disclosure. */
  technical_detail?: string;
  /** DEV-34: other cards of this consultation whose premises this card's choice changes — and why. */
  related?: { key: string; why?: string }[];
  options: ConsultOption[];
  rationale?: string;
  allow_free_text?: boolean;
}
/**
 * Veta o pôvode rozhodnutia (ICCINT-123). `null` pre staré karty bez toho údaja — vymyslená veta by
 * bola horšia než žiadna, lebo by tvrdila niečo o pôvode, ktorý nepoznáme.
 *
 * DEV-4: pri dôsledku menuje rozhodnutie jeho OTÁZKOU a voľbou Manažéra (`context` z backendu, ktorý číta celý
 * záznam) — `origin_of` je od DEV-3 kľúč rozhodnutia a kľúč Manažérovi nič nepovie, preto sa nikdy nevypisuje.
 */
function povodVeta(d: ConsultDecision, context: DecisionCardContext | undefined): string | null {
  switch (d.origin) {
    case "objav":
      return "Nález, ktorý tu bol vždy — nevznikol z tvojho rozhodnutia.";
    case "dosledok": {
      const question = context?.origin_of_question;
      if (!question) return "Vyplýva z niektorého tvojho skoršieho rozhodnutia.";
      const choice = context?.origin_of_choice;
      return `Vyplýva z tvojho rozhodnutia „${question}“${choice ? ` (tvoja voľba: ${choice})` : ""}.`;
    }
    case "odklad":
      return "Toto sme vedome odložili — teraz je na rade.";
    default:
      return null;
  }
}

/**
 * DEV-34: the cards this choice hangs together with, named by number and question — so two decisions that
 * would contradict each other are seen while deciding, not one review round later. A link to a card that is
 * not in the consultation is skipped (the engine rejects such cards; old records may still carry one).
 */
function suvisiaceVety(decisions: ConsultDecision[], d: ConsultDecision): string[] {
  return (d.related ?? []).flatMap((rel) => {
    const i = decisions.findIndex((x) => x.key === rel.key);
    const other = decisions[i];
    if (!other || rel.key === d.key) return [];
    const why = rel.why?.trim();
    return [`Súvisí s kartou ${i + 1} („${other.question}“)${why ? `: ${why}` : "."}`];
  });
}

/**
 * Koľko UŽ UZAVRETÝCH rozhodnutí by sa zmenou tohto rozhodnutia znovu otvorilo (ICCINT-123).
 *
 * Počíta backend z celého záznamu (DEV-4): rozhodnutia s `origin="dosledok"` a `origin_of` = kľúč tohto, ktoré
 * už majú zapísanú odpoveď — v ktoromkoľvek kole. Nezodpovedané karty toho istého kola uzavreté nie sú a nepočítajú
 * sa. Manažér to má vedieť PRI voľbe, nie až po nej.
 *
 * ⚠️⚠️ AKO ÚDAJ, NIKDY AKO ODPORÚČANIE. Toto je najdôležitejšia veta celého tiketu. Pri NEX Inbox
 * v1.5.0 boli DVE miesta, kde bola drahšia cesta správna — zmena skladania textu namiesto obchádzky
 * a široký rozsah pri početných kontrolách. Keby sa z tohto čísla stal tlak na lacnejšiu voľbu,
 * vyrobí polovičné opravy, teda presne to, čo tá verzia opravovala. Číslo sa preto len ZOBRAZUJE:
 * nemení poradie možností ani značku „odporúčané“.
 */
function znovuOtvara(context: DecisionCardContext | undefined): number {
  return context?.reopens_closed ?? 0;
}

/** Slovak plural after a number: 1 uzavreté rozhodnutie, 2–4 uzavreté rozhodnutia, 5+ uzavretých rozhodnutí. */
function uzavreteRozhodnutia(n: number): string {
  if (n === 1) return "uzavreté rozhodnutie";
  if (n >= 2 && n <= 4) return "uzavreté rozhodnutia";
  return "uzavretých rozhodnutí";
}

interface Consultation {
  id: string;
  intro?: string;
  /** Why the consultation came — the engine writes it (DEV-106); the review-with-Poradca request depends on it. */
  source?: string;
  decisions: ConsultDecision[];
  /**
   * ICCINT-72: koľké kolo konzultácie o tej istej veci to je a koľko ich môže prísť.
   *
   * „Rozhodnutie 3 z 5“ nižšie hovorí o rozhodnutiach v TOMTO kole — ľahko sa to zamení za kolá a znie
   * to ako koniec, hoci koniec to nie je. Na v0.2.0 prešiel Manažér piatimi kolami za dva a pol hodiny
   * (23 rozhodnutí) bez toho, aby vedel, že päť je strop; číslo sa objavilo až pri eskalácii, teda keď
   * už bolo po všetkom. Staršie záznamy tie údaje nemajú — vtedy sa riadok neukáže.
   */
  round?: number | null;
  round_max?: number | null;
}

// The latest message CARRYING a decision queue — the ACTUAL blocking message (criterion 4) — plus its `seq`,
// so answers can be SEQ-scoped below (mirrors backend `_latest_consultation`). "Latest" = the highest seq, not
// the array-last element, so the ordering of `recent_messages` can't pick a stale consultation.
//
// ICCINT-26: keyed on the QUEUE, not on `kind`. The agent does attach a complete queue to a `question` block —
// on 24.08.2026 it attached five decisions that way — and a card queue that the backend now acts on must be a
// card queue this screen can show. Filtering on the label would leave the cards on record and off the screen.
function latestConsultation(messages: PipelineMessage[]): { consultation: Consultation; seq: number } | null {
  let best: { consultation: Consultation; seq: number } | null = null;
  for (const m of messages) {
    if (!m) continue;
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
//
// DEV-107: an answer the backend recorded as ``held`` (a verification card held until he decides) does not close the
// card; the decision that comes after it does.
function answeredLabels(messages: PipelineMessage[], afterSeq: number): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of messages) {
    const cd = decisionAnswer(m, afterSeq);
    if (cd?.key && !cd.held) out[cd.key] = cd.label ?? "—";
  }
  return out;
}

interface DecisionAnswer {
  key?: string;
  label?: string;
  held?: boolean;
}

function decisionAnswer(m: PipelineMessage | undefined, afterSeq: number): DecisionAnswer | null {
  if (!m || m.kind !== "answer" || m.seq <= afterSeq) return null;
  return (m.payload as { consultation_decision?: DecisionAnswer } | null)?.consultation_decision ?? null;
}

/** DEV-107: is the newest answer to this card a hold? Then the card waits — and says so. */
function heldNow(messages: PipelineMessage[], key: string, afterSeq: number): boolean {
  let newest: { seq: number; held: boolean } | null = null;
  for (const m of messages) {
    const cd = decisionAnswer(m, afterSeq);
    if (cd?.key === key && (newest === null || m.seq > newest.seq)) newest = { seq: m.seq, held: !!cd.held };
  }
  return newest?.held ?? false;
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
  // DEV-26: the card's instruction for the AI partner is a writing surface like any other — it takes
  // Poradca's instruction during a consultation, keeps its lines, and survives a trip to another screen.
  // One draft per card, so an instruction written for one decision never rides along with the next one.
  const currentKey = useMemo(() => {
    const decisions = latest?.consultation.decisions ?? [];
    return decisions.find((d) => !(d.key in answered))?.key ?? null;
  }, [latest, answered]);
  const noteDraft = useDraft(currentKey ? draftKey(`karta.${currentKey}`, versionId) : null);
  // DEV-32: his own answer to a card („Iná odpoveď") is a writing surface too — it survives leaving the screen.
  const freeTextDraft = useDraft(currentKey ? draftKey(`karta-odpoved.${currentKey}`, versionId) : null);
  const freeText = freeTextDraft.text;
  const setFreeText = freeTextDraft.setText;
  const note = noteDraft.text;
  const noteRef = useAutoGrowTextarea(note);
  const { fromPoradca, dismiss: dismissPoradca } = usePoradcaHandoff({
    surface: "karta",
    versionId,
    live:
      !!versionId &&
      !!currentKey &&
      !!board?.available_actions?.includes("decide") &&
      inputOwner(board?.state) === "karta",
    text: note,
    setText: noteDraft.setText,
    restored: noteDraft.restored,
  });
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
  // ICCINT-124 — smer sa počíta zo záznamu, nie z počítadla: uzavreté rozhodnutia a to, ako v
  // jednotlivých kolách klesali (alebo stúpali) blokujúce nálezy. DEV-5: z celého záznamu verzie (backend),
  // nie z chvosta posledných správ, a k tomu veta o tom, čo posledné kolo získalo.
  const smerRiadok = smerDoRiadku(board.consultation_direction);
  const ziskRiadok = ziskKola(board.consultation_direction?.last_round);
  const current = idx >= 0 ? consultation.decisions[idx] : null;
  if (!current) return null; // all decided → the apply is dispatching; the card disappears
  // DEV-4: where this card's question came from and what a different choice reopens — read from the whole record.
  const context = board.decision_context?.[current.key];
  const origin = povodVeta(current, context);
  const reopens = znovuOtvara(context);
  const held = latest ? heldNow(messages, current.key, latest.seq) : false;

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
      // DEV-107: a hold sends nothing — what stands in the fields stays there for the decision still to come.
      if (!(latest && heldNow(nextBoard.recent_messages ?? [], current.key, latest.seq))) {
        freeTextDraft.clear();
        noteDraft.clear();
        dismissPoradca();
        setShowFree(false);
      }
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
        {/* ICCINT-72: koľké kolo, a koľko ich ešte môže prísť. Bez toho Manažér nevie, či je na začiatku,
            alebo pred posledným kolom — a či sa vôbec oplatí čakať ďalšie. */}
        {/* ICCINT-124: SMER, nie stav. „rozhodni 1 zo 4 (kolo 4 z 5)“ hovorí, koľko zostáva;
            nehovorí, či je to lepšie než pred kolom. Pri NEX Inbox v1.5.0 išli blokujúce 5 → 1 → 0
            → 1 → 0 a dvanásť nálezov sa uzavrelo — ten obraz sa musel skladať ručne z databázy.
            ⚠️ Riadok NEHODNOTÍ, vypisuje: rad 0 → 2 → 5 sa nedá prečítať ako pokrok. */}
        {smerRiadok && (
          <div className="px-4 pb-1 pt-2 text-[11px] tabular-nums text-[var(--color-text-secondary)]">
            {smerRiadok}
          </div>
        )}
        {/* DEV-5: čo kolo ZÍSKALO, nie čo pribudlo — nález, ktorý by prešiel všetkými skúškami, by sa inak
            prejavil až u zákazníka. */}
        {ziskRiadok && <p className="px-4 pb-1 text-[11px] text-[var(--color-text-secondary)]">{ziskRiadok}</p>}

        {typeof consultation.round === "number" && typeof consultation.round_max === "number" && (
          <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">
            Kolo konzultácie {consultation.round} z {consultation.round_max}
            {consultation.round >= consultation.round_max
              ? " — posledné. Ďalšie kolo nebude, potom rozhoduješ sám."
              : ""}
          </p>
        )}
        {/* ICCINT-97: kolá opráv po Verifikácii strop NEMAJÚ — kým na karty odpovedá človek, počítadlo
            samočinnej slučky sa nuluje, a to je správne. Ukazuje sa preto číslo bez „z piatich“:
            napísať tam strop, ktorý neplatí, by bola lož. Bez čísla prešiel Manažér 09.09.2026 šesť
            kôl a nikde sa to nedozvedel. */}
        {typeof consultation.round === "number" && typeof consultation.round_max !== "number" && (
          <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">
            Kolo opráv {consultation.round} — kým odpovedáš ty, ďalšie kolá prísť môžu.
          </p>
        )}
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
          {held && (
            <p role="status" className="mt-1 text-[11px] font-medium text-[var(--color-text-secondary)]">
              Podržané — karta počká na tvoje rozhodnutie.
            </p>
          )}
          {/* ICCINT-123: jedna veta o pôvode. Nemení to POČET otázok — mení to, čo si o tom počte
              Manažér myslí. Pri NEX Inbox v1.5.0 vyzeralo štvrté kolo najhoršie a bol to čistý objav:
              nevyvolalo ho žiadne jeho rozhodnutie, a z karty to nijako nevyplývalo. */}
          {origin && <p className="mt-1 text-[11px] italic text-[var(--color-text-muted)]">{origin}</p>}
          {suvisiaceVety(consultation.decisions, current).map((veta) => (
            <p key={veta} className="mt-1 text-[11px] text-[var(--color-text-secondary)]">
              {veta}
            </p>
          ))}
          {current.explanation && (
            <p className="mt-1 whitespace-pre-line text-xs text-[var(--color-text-secondary)]">
              {current.explanation}
            </p>
          )}
          {/* v4.0.11: the jargon (paths / codes / repro) is tucked behind a disclosure so the explanation
              above stays plain for a non-expert — nothing lost, one click away. */}
          {current.technical_detail && (
            <details className="mt-1.5">
              <summary className="cursor-pointer text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]">
                Technický detail
              </summary>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded bg-[var(--color-canvas)] p-2 text-[11px] text-[var(--color-text-muted)]">
                {current.technical_detail}
              </pre>
            </details>
          )}

          {/* DEV-103/DEV-106: every card goes to Poradca as it stands — nothing to copy, nothing to type. */}
          <ReviewWithPoradcaButton
            versionId={versionId}
            card={{
              position: idx + 1,
              total,
              round: typeof consultation.round === "number" ? consultation.round : null,
              roundMax: typeof consultation.round_max === "number" ? consultation.round_max : null,
              request: reviewRequestFor(consultation.source),
              question: current.question,
              origin: origin ?? undefined,
              explanation: current.explanation,
              technicalDetail: current.technical_detail,
              options: current.options.map((o) => ({ label: o.label, recommended: o.recommended })),
              rationale: current.rationale,
            }}
          />

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

          {/* ICCINT-123: cena voľby PRI voľbe, nie po nej. Údaj, nie odporúčanie — poradím ani
              značkou „odporúčané“ nehýbe. */}
          {reopens > 0 && (
            <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
              {`Zmena tejto voľby znovu otvára ${reopens} ${uzavreteRozhodnutia(reopens)}.`}
            </p>
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
              data-draft="karta-odpoved"
              spellCheck={true}
              lang="sk"
              value={freeText}
              onChange={(e) => setFreeText(e.target.value)}
              placeholder="Tvoja odpoveď…"
              rows={2}
              className="mt-2 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-xs text-[var(--color-text-primary)] focus:border-primary-500 focus:outline-none"
            />
          )}

          {fromPoradca && (
            <p className="mt-2 text-[11px] font-medium text-[var(--color-accent-primary)]">{FROM_PORADCA_LABEL}</p>
          )}
          {held && note.trim() && (
            <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">Text v poli sa neposlal — ostáva tu na neskôr.</p>
          )}
          {noteDraft.restored && !fromPoradca && !held && (
            <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">{RESTORED_DRAFT_LABEL}</p>
          )}
          <textarea
            data-draft="karta"
            lang="sk"
            spellCheck={true}
            ref={noteRef}
            value={note}
            onChange={(e) => {
              noteDraft.setText(e.target.value);
              if (fromPoradca) dismissPoradca(); // he edited it — the text is his now
            }}
            rows={1}
            placeholder="Pokyn pre AI partnera (nepovinné) — napíš, čo má spraviť"
            className="mt-2 w-full resize-none rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-xs text-[var(--color-text-primary)] focus:border-primary-500 focus:outline-none"
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

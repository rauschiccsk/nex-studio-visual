// BatchReviewSummary — the fast path with Poradca (DEV-483): every card of a consultation with the Staviteľ's proposal
// and Poradca's beside it, approved in one go.
//
// Director 21.11.2026: „Následne mi kompaktne a prehľadne ukazál by otázku a návrh staviteľa a taktiež návrh Poradcu.
// … zo 100 otázok je len maximálne 2-3 keď nesúhlasím s poradcom. … mne by dali len súhrn na schválenie. Ak niečim
// nebudem súhlasiť potom to budem riešiť Staviteľom a Poradcom osobitne.“
//
// DEV-486 — the summary is the only place to decide, and one button approves every card. Director 21.11.2026: „Rýchla
// cesta má byť všetky otázky overené poradcom. Chcem videiť na jednom mieste každú jednu otázku, kde je uvedené návrh
// Staviteľa a návrh Poradcu. Ja to prečítam a ak všetko mi vyhovuje s jednám tlačítkom schválim všetky otázky. Chcem
// však mať možnosť, že v prípade môjho nesúhlasu podrobne rozobrať a prekonzuľtoať danú otázku. … pri overení Poradca
// pri cca 80% otázok mal aj nejaké dodatočné informácie pre Staviteľa, tu som to nevidel.“
//
// So each row shows the question, the Staviteľ's proposal, Poradca's with his reason and his instruction for the
// Staviteľ. „Schváliť všetko“ sends the ordinary `decide` for every card: Poradca's option (or his own decision) and
// his instruction as the note (`via=batch_review`, checked again by the server) — where the two disagree, Poradca's
// proposal, he is the one who verified it. A card the Manažér does not agree with he takes out with „Prekonzultovať“:
// its card opens under the row — the same card as everywhere, with „Overiť s Poradcom“ — and he decides it himself;
// a card related to it waits until then. A card Poradca left out opens the same way on its own.
//
// The cards one by one (DecisionCardsBar) are drawn from here, never beside the summary: when there is no summary
// (the account's switch is off, another kind of consultation) and when Poradca did not finish it — the build never
// stands. While Poradca answers, only that is said.

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCheck, Loader2, MessageSquare, RotateCcw } from "lucide-react";

import DecisionCardsBar from "@/components/controlCenter/DecisionCardsBar";
import ErrorNote from "@/components/common/ErrorNote";
import { getBatchReviewApi, reviewAgainApi, type BatchReview, type BatchReviewRow } from "@/services/api/batchReview";
import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { approvable, decidesHimself, POLL_MS, proposed } from "@/lib/batchReview";
import { humanizeApiError, type HumanError } from "@/services/apiError";

const STATE_WORDS: Record<BatchReviewRow["state"], string> = {
  agree: "✓ zhoda",
  disagree: "⚠ Poradca navrhuje inak",
  missing: "— bez posudku",
  waiting: "… posudzuje",
  decided: "rozhodnuté",
};

const NOTE = "mb-2 rounded border border-[var(--color-border-default)] px-3 py-2 text-xs text-[var(--color-text-secondary)]";
const SMALL_BUTTON =
  "inline-flex items-center gap-1 rounded border border-[var(--color-border-default)] px-2 py-1 text-[11px] text-[var(--color-text-secondary)] hover:border-primary-500 disabled:opacity-40 pointer-coarse:min-h-11";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  onBoard: (board: PipelineBoard) => void;
}

/** What „Schváliť všetko“ sends for a card — Poradca's proposal, his instruction as the note. */
function approvalOf(row: BatchReviewRow) {
  const note = row.poradca_instruction ?? undefined;
  return row.option_id
    ? { decision_key: row.key, option_id: row.option_id, note, via: "batch_review" }
    : { decision_key: row.key, free_text: row.free_text ?? "", note, via: "batch_review" };
}

export default function BatchReviewSummary({ board, versionId, onBoard }: Props) {
  const deciding = !!board?.available_actions?.includes("decide");
  // `undefined` — not read yet: nothing is drawn, so no card flashes up before the summary takes its place.
  const [review, setReview] = useState<BatchReview | null | undefined>(undefined);
  const [consulted, setConsulted] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const seen = board?.recent_messages?.length ?? 0;

  const load = useCallback(async () => {
    try {
      setReview(await getBatchReviewApi(versionId));
    } catch {
      setReview(null); // the summary is a help; without it the cards wait one by one
    }
  }, [versionId]);

  useEffect(() => {
    if (!deciding) {
      setReview(undefined);
      return;
    }
    void load();
  }, [deciding, load, seen]);

  useEffect(() => {
    if (!deciding || review?.status !== "running") return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [deciding, review?.status, load]);

  const rows = useMemo(() => review?.rows ?? [], [review?.rows]);
  const { keys, heldBy } = useMemo(() => approvable(rows, consulted), [rows, consulted]);
  const proposals = useMemo(() => Object.fromEntries(rows.map((r) => [r.key, r])), [rows]);

  async function again() {
    setBusy(true);
    setError(null);
    try {
      setReview(await reviewAgainApi(versionId));
      setConsulted(new Set());
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Poradcovi sa karty nepodarilo dať znova"));
    } finally {
      setBusy(false);
    }
  }

  if (!deciding || review === undefined) return null;

  if (review === null) return <DecisionCardsBar board={board} versionId={versionId} onBoard={onBoard} />;

  const againButton = (
    <button type="button" onClick={() => void again()} disabled={busy} className={SMALL_BUTTON}>
      <RotateCcw className="h-3 w-3" aria-hidden="true" />
      Posúdiť znova
    </button>
  );

  if (review.status === "failed") {
    return (
      <>
        <div role="status" className={`${NOTE} flex flex-wrap items-center justify-between gap-2`}>
          <span>{review.failure}</span>
          {againButton}
        </div>
        <ErrorNote error={error} className="mb-2" />
        <DecisionCardsBar board={board} versionId={versionId} onBoard={onBoard} />
      </>
    );
  }
  if (review.status === "running") {
    return (
      <p role="status" className={`${NOTE} flex items-center gap-2`}>
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        Poradca posudzuje naraz všetky karty ({rows.length}) — súhrn sa ukáže tu.
      </p>
    );
  }

  const counts = review.counts;
  const next = rows.find((r) => decidesHimself(r, consulted)) ?? null;
  const open = rows.filter((r) => r.state !== "decided").length;
  const toggle = (key: string) =>
    setConsulted((prev) => {
      const nextSet = new Set(prev);
      if (nextSet.has(key)) nextSet.delete(key);
      else nextSet.add(key);
      return nextSet;
    });

  async function approve() {
    setBusy(true);
    setError(null);
    try {
      let latest: PipelineBoard | null = null;
      for (const row of rows) {
        if (!keys.has(row.key)) continue;
        latest = await postPipelineActionApi(versionId, { action: "decide", payload: approvalOf(row) });
      }
      if (latest) onBoard(latest);
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Schválenie súhrnu zlyhalo"));
    } finally {
      setBusy(false);
      void load();
    }
  }

  return (
    <section
      aria-label="Súhrn s Poradcom"
      className="mb-2 rounded-lg border border-[var(--color-accent-primary)] bg-[var(--color-surface)] px-3 py-2"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">Súhrn s Poradcom</h3>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-[var(--color-text-muted)]">
            zhoda {counts.agree ?? 0} · Poradca inak {counts.disagree ?? 0}
            {counts.missing ? ` · bez posudku ${counts.missing}` : ""}
            {counts.decided ? ` · rozhodnuté ${counts.decided}` : ""}
          </span>
          {againButton}
        </div>
      </div>
      <ul className="mt-2 divide-y divide-[var(--color-border-default)]">
        {rows.map((row) => {
          const waitsFor = heldBy.get(row.key);
          const consulting = consulted.has(row.key) && row.state !== "decided";
          const later = next !== null && row.key !== next.key && decidesHimself(row, consulted);
          return (
            <li key={row.key} className="py-2 text-xs">
              <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                <span className="font-medium text-[var(--color-text-primary)]">
                  {row.number}. {row.question}
                </span>
                <span className="whitespace-nowrap text-[11px] text-[var(--color-text-secondary)]">
                  {waitsFor !== undefined
                    ? `⏸ čaká na kartu ${waitsFor}`
                    : consulting
                      ? "💬 konzultuješ"
                      : STATE_WORDS[row.state]}
                </span>
              </div>
              <p className="text-[var(--color-text-secondary)]">
                <span className="text-[var(--color-text-muted)]">Staviteľ:</span> {row.builder_label ?? "—"}
              </p>
              <p className="text-[var(--color-text-secondary)]">
                <span className="text-[var(--color-text-muted)]">
                  {row.state === "decided" ? "Rozhodnuté:" : "Poradca:"}
                </span>{" "}
                {row.state === "decided" ? (row.decided_label ?? "—") : (row.poradca_label ?? "—")}
                {row.poradca_why && row.state !== "decided" && (
                  <span className="text-[var(--color-text-muted)]"> — {row.poradca_why}</span>
                )}
              </p>
              {row.poradca_instruction && row.state !== "decided" && (
                <p className="whitespace-pre-line text-[var(--color-text-secondary)]">
                  <span className="text-[var(--color-text-muted)]">Pokyn pre Staviteľa:</span> {row.poradca_instruction}
                </p>
              )}
              {row.state === "missing" && (
                <p className="text-[11px] text-[var(--color-text-muted)]">
                  Poradca túto kartu neposúdil — rozhodni ju na karte, alebo daj karty posúdiť znova.
                </p>
              )}
              {waitsFor !== undefined && (
                <p className="text-[11px] text-[var(--color-text-muted)]">
                  Súvisí s kartou {waitsFor} — schváli sa, keď kartu {waitsFor} rozhodneš.
                </p>
              )}
              {later && (
                <p className="text-[11px] text-[var(--color-text-muted)]">Rozhodneš ju tu po karte {next.number}.</p>
              )}
              {proposed(row) && (
                <div className="mt-1">
                  <button
                    type="button"
                    onClick={() => toggle(row.key)}
                    disabled={busy}
                    aria-pressed={consulting}
                    aria-label={`${consulting ? "Vrátiť do schválenia" : "Prekonzultovať"} kartu ${row.number}`}
                    className={SMALL_BUTTON}
                  >
                    <MessageSquare className="h-3 w-3" aria-hidden="true" />
                    {consulting ? "Vrátiť do schválenia" : "Prekonzultovať"}
                  </button>
                </div>
              )}
              {next?.key === row.key && (
                <DecisionCardsBar
                  board={board}
                  versionId={versionId}
                  onBoard={onBoard}
                  proposals={proposals}
                  only={[row.key]}
                  embedded
                />
              )}
            </li>
          );
        })}
      </ul>
      <ErrorNote error={error} className="mt-2" />
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <span className="text-[11px] text-[var(--color-text-muted)]">
          Schválením dostane Staviteľ pri každej karte návrh Poradcu aj jeho pokyn.
          {next ? ` Kartu ${next.number} rozhodneš vyššie, pod jej riadkom.` : ""}
        </span>
        <button
          type="button"
          onClick={() => void approve()}
          disabled={busy || keys.size === 0}
          className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-40"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCheck className="h-3.5 w-3.5" />}
          {keys.size === open ? `Schváliť všetko (${keys.size})` : `Schváliť ostatné (${keys.size})`}
        </button>
      </div>
    </section>
  );
}

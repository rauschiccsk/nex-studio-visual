// BatchReviewSummary — the fast path with Poradca (DEV-483): the cards of a consultation, the Staviteľ's proposal and
// Poradca's beside it, approved in one go.
//
// Director 21.11.2026: „Následne mi kompaktne a prehľadne ukazál by otázku a návrh staviteľa a taktiež návrh Poradcu.
// … zo 100 otázok je len maximálne 2-3 keď nesúhlasím s poradcom. … mne by dali len súhrn na schválenie. Ak niečim
// nebudem súhlasiť potom to budem riešiť Staviteľom a Poradcom osobitne.“
//
// DEV-486 — while the fast path is on, the summary is the only place to decide. Director 21.11.2026: „Ak je zapnutá
// rýchla cesta, potom nechcem mať súčasne na obrazovke aj otázky po jednom - je to mätúce.“ So the cards one by one
// (DecisionCardsBar) are drawn from here, never beside the summary: when there is no summary (the account's switch is
// off, another kind of consultation) and when Poradca did not finish it — the build never stands. While Poradca
// answers, only that is said.
//
// Agreements are pre-checked; „Schváliť označené“ sends the ordinary `decide` for each (`via=batch_review`, checked
// again by the server). A card the Manažér decides himself — his alone, a disagreement, one Poradca left out, an
// agreement he unchecked — opens under its row, one at a time: the same card as everywhere (options, both proposals,
// details, his own decision, „Overiť s Poradcom“). A card related to it (DEV-34) has no checkbox while it waits — its
// row says on which card — and is checked the moment that card is decided.

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCheck, Loader2 } from "lucide-react";

import DecisionCardsBar from "@/components/controlCenter/DecisionCardsBar";
import ErrorNote from "@/components/common/ErrorNote";
import { getBatchReviewApi, type BatchReview, type BatchReviewRow } from "@/services/api/batchReview";
import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { approvable, decidesHimself, POLL_MS } from "@/lib/batchReview";
import { humanizeApiError, type HumanError } from "@/services/apiError";

const STATE_WORDS: Record<BatchReviewRow["state"], string> = {
  agree: "✓ zhoda",
  disagree: "⚠ nezhoda",
  manager: "👤 rozhoduješ ty",
  missing: "— bez posudku",
  waiting: "… posudzuje",
  decided: "rozhodnuté",
};

const NOTE = "mb-2 rounded border border-[var(--color-border-default)] px-3 py-2 text-xs text-[var(--color-text-secondary)]";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  onBoard: (board: PipelineBoard) => void;
}

export default function BatchReviewSummary({ board, versionId, onBoard }: Props) {
  const deciding = !!board?.available_actions?.includes("decide");
  // `undefined` — not read yet: nothing is drawn, so no card flashes up before the summary takes its place.
  const [review, setReview] = useState<BatchReview | null | undefined>(undefined);
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set());
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
  const { keys, heldBy } = useMemo(() => approvable(rows, unchecked), [rows, unchecked]);
  const proposals = useMemo(() => Object.fromEntries(rows.map((r) => [r.key, r])), [rows]);

  if (!deciding || review === undefined) return null;

  if (review === null) return <DecisionCardsBar board={board} versionId={versionId} onBoard={onBoard} />;

  if (review.status === "failed") {
    return (
      <>
        <p role="status" className={NOTE}>
          {review.failure}
        </p>
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
  const next = rows.find((r) => decidesHimself(r, unchecked)) ?? null;
  const toggle = (key: string) =>
    setUnchecked((prev) => {
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
        latest = await postPipelineActionApi(versionId, {
          action: "decide",
          payload: { decision_key: row.key, option_id: row.option_id, via: "batch_review" },
        });
      }
      if (latest) onBoard(latest);
      setUnchecked(new Set());
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
        <span className="text-[11px] text-[var(--color-text-muted)]">
          zhoda {counts.agree ?? 0} · nezhoda {counts.disagree ?? 0} · rozhoduješ ty {counts.manager ?? 0}
          {counts.missing ? ` · bez posudku ${counts.missing}` : ""}
          {counts.decided ? ` · rozhodnuté ${counts.decided}` : ""}
        </span>
      </div>
      <ul className="mt-2 divide-y divide-[var(--color-border-default)]">
        {rows.map((row) => {
          const waitsFor = heldBy.get(row.key);
          const later = next !== null && row.key !== next.key && decidesHimself(row, unchecked);
          return (
            <li key={row.key} className="py-1.5 text-xs">
              <div className="flex gap-2">
                <span className="flex w-6 shrink-0 justify-center pt-0.5">
                  {row.state === "agree" && waitsFor === undefined ? (
                    <label className="flex items-start justify-center pointer-coarse:min-h-11 pointer-coarse:min-w-11">
                      <input
                        type="checkbox"
                        checked={keys.has(row.key)}
                        disabled={busy}
                        onChange={() => toggle(row.key)}
                        aria-label={`Schváliť kartu ${row.number}`}
                        className="pointer-coarse:h-6 pointer-coarse:w-6"
                      />
                    </label>
                  ) : null}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                    <span className="font-medium text-[var(--color-text-primary)]">
                      {row.number}. {row.question}
                    </span>
                    <span className="whitespace-nowrap text-[11px] text-[var(--color-text-secondary)]">
                      {waitsFor !== undefined ? `⏸ čaká na kartu ${waitsFor}` : STATE_WORDS[row.state]}
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
                  {waitsFor !== undefined && (
                    <p className="text-[11px] text-[var(--color-text-muted)]">
                      Súvisí s kartou {waitsFor} — zaškrtne sa, keď kartu {waitsFor} rozhodneš.
                    </p>
                  )}
                  {later && (
                    <p className="text-[11px] text-[var(--color-text-muted)]">
                      Rozhodneš ju tu po karte {next.number}.
                    </p>
                  )}
                </div>
              </div>
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
          {next
            ? `Kartu ${next.number} rozhodneš vyššie, pod jej riadkom — zhody schváliš tlačidlom.`
            : "Zhody schváliš jedným klikom."}
        </span>
        <button
          type="button"
          onClick={() => void approve()}
          disabled={busy || keys.size === 0}
          className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-40"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCheck className="h-3.5 w-3.5" />}
          Schváliť označené ({keys.size})
        </button>
      </div>
    </section>
  );
}

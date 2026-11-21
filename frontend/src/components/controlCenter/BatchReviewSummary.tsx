// BatchReviewSummary — the fast path with Poradca (DEV-483): the cards of a consultation, the Staviteľ's proposal and
// Poradca's beside it, approved in one go.
//
// Director 21.11.2026: „Následne mi kompaktne a prehľadne ukazál by otázku a návrh staviteľa a taktiež návrh Poradcu.
// … zo 100 otázok je len maximálne 2-3 keď nesúhlasím s poradcom. … mne by dali len súhrn na schválenie. Ak niečim
// nebudem súhlasiť potom to budem riešiť Staviteľom a Poradcom osobitne.“
//
// Shown only while the build stands on cards and Poradca was asked about them (the account's switch). Agreements are
// pre-checked; a card that belongs to the Manažér alone, a disagreement and a card Poradca left out are never. A card
// he leaves for himself takes its related cards (DEV-34) out of the approval too — their premises may change.
// „Schváliť označené“ sends the ordinary `decide` for each (`via=batch_review`, checked again by the server); the
// rest stay as cards below, one by one, with both proposals.

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCheck, Loader2 } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import { getBatchReviewApi, type BatchReview, type BatchReviewRow } from "@/services/api/batchReview";
import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { approvable, POLL_MS } from "@/lib/batchReview";
import { humanizeApiError, type HumanError } from "@/services/apiError";

const STATE_WORDS: Record<BatchReviewRow["state"], string> = {
  agree: "✓ zhoda",
  disagree: "⚠ nezhoda",
  manager: "👤 rozhoduješ ty",
  missing: "— bez posudku",
  waiting: "… posudzuje",
  decided: "rozhodnuté",
};

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  onBoard: (board: PipelineBoard) => void;
  /** Poradca's word per card, for the cards left to decide one by one. */
  onProposals?: (rows: Record<string, BatchReviewRow>) => void;
}

export default function BatchReviewSummary({ board, versionId, onBoard, onProposals }: Props) {
  const deciding = !!board?.available_actions?.includes("decide");
  const [review, setReview] = useState<BatchReview | null>(null);
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const seen = board?.recent_messages?.length ?? 0;

  const load = useCallback(async () => {
    try {
      const found = await getBatchReviewApi(versionId);
      setReview(found);
      onProposals?.(Object.fromEntries((found?.rows ?? []).map((r) => [r.key, r])));
    } catch {
      setReview(null); // the summary is a help; without it the cards wait one by one
    }
  }, [versionId, onProposals]);

  useEffect(() => {
    if (!deciding) {
      setReview(null);
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

  if (!deciding || review === null) return null;

  if (review.status === "failed") {
    return (
      <p role="status" className="mb-2 rounded border border-[var(--color-border-default)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
        {review.failure}
      </p>
    );
  }
  if (review.status === "running") {
    return (
      <p role="status" className="mb-2 flex items-center gap-2 rounded border border-[var(--color-border-default)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        Poradca posudzuje naraz všetky karty ({rows.length}) — súhrn sa ukáže tu. Karty môžeš medzitým rozhodovať aj po
        jednej.
      </p>
    );
  }

  const counts = review.counts;
  const toggle = (key: string) =>
    setUnchecked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
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
        {rows.map((row) => (
          <li key={row.key} className="flex gap-2 py-1.5 text-xs">
            <span className="flex w-6 shrink-0 justify-center pt-0.5">
              {row.state === "agree" ? (
                <label className="flex items-start justify-center pointer-coarse:min-h-11 pointer-coarse:min-w-11">
                  <input
                    type="checkbox"
                    checked={keys.has(row.key)}
                    disabled={busy || heldBy.has(row.key)}
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
                <span className="whitespace-nowrap text-[11px] text-[var(--color-text-secondary)]">{STATE_WORDS[row.state]}</span>
              </div>
              <p className="text-[var(--color-text-secondary)]">
                <span className="text-[var(--color-text-muted)]">Staviteľ:</span> {row.builder_label ?? "—"}
              </p>
              <p className="text-[var(--color-text-secondary)]">
                <span className="text-[var(--color-text-muted)]">{row.state === "decided" ? "Rozhodnuté:" : "Poradca:"}</span>{" "}
                {row.state === "decided" ? (row.decided_label ?? "—") : (row.poradca_label ?? "—")}
                {row.poradca_why && row.state !== "decided" && (
                  <span className="text-[var(--color-text-muted)]"> — {row.poradca_why}</span>
                )}
              </p>
              {heldBy.has(row.key) && (
                <p className="text-[11px] text-[var(--color-text-muted)]">
                  Súvisí s kartou {heldBy.get(row.key)}, ktorú rozhoduješ sám — rozhodni ju na karte.
                </p>
              )}
            </div>
          </li>
        ))}
      </ul>
      <ErrorNote error={error} className="mt-2" />
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <span className="text-[11px] text-[var(--color-text-muted)]">
          Ostatné karty rozhodneš nižšie po jednej — s návrhom Staviteľa aj Poradcu.
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

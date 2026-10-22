// PriceEstimatePanel — the orientačná cena of a version (DEV-154), on its page once Návrh gave it a plan.
//
// Director 20.10.2026: „Jedná sa skutočne len o odhad, aby zákazník vedel sa rozhodnúť, či objedná daný projekt.“ The
// estimate is the tasks of the plan × the tokens a task of the finished versions delivered × the version's rates; the
// ± is how far those versions strayed from their mean. Resting on few versions it says so, and it is never an invoice.

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import {
  getPriceEstimateApi,
  type PriceEstimate,
} from "@/services/api/priceEstimate";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import {
  amountWords,
  fromVersionsWords,
  onVersionsWords,
  tasksWords,
} from "@/lib/priceEstimate";
import { formatEur } from "@/utils/format";

const tokens = (n: number) => n.toLocaleString("sk-SK");

export default function PriceEstimatePanel({
  versionId,
}: {
  versionId: string;
}) {
  const [estimate, setEstimate] = useState<PriceEstimate | null | undefined>(
    undefined,
  );
  const [error, setError] = useState<HumanError | null>(null);

  useEffect(() => {
    let cancelled = false;
    setEstimate(undefined);
    setError(null);
    getPriceEstimateApi(versionId)
      .then((e) => !cancelled && setEstimate(e))
      .catch(
        (e: unknown) =>
          !cancelled &&
          setError(
            humanizeApiError(e, "Orientačnú cenu sa nepodarilo načítať"),
          ),
      );
    return () => {
      cancelled = true;
    };
  }, [versionId]);

  if (error) {
    return (
      <section className="mt-4 rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-5">
        <ErrorNote error={error} />
      </section>
    );
  }
  if (estimate === undefined) {
    return (
      <div className="mt-4 flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Počítam orientačnú
        cenu…
      </div>
    );
  }
  if (estimate === null) return null;

  const pool = estimate.first_version
    ? "prvých verzií projektov, ktoré stavajú kostru aplikácie"
    : "ďalších verzií projektov";
  return (
    <section
      aria-label="Orientačná cena"
      className="mt-4 rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-5"
    >
      <div className="text-[10px] uppercase tracking-widest text-[var(--color-text-muted)]">
        Orientačná cena
      </div>
      <div
        className="mt-1 text-xl font-semibold text-[var(--color-text-primary)]"
        title={
          estimate.spread_pct != null
            ? "Plus-mínus hovorí, o koľko sa hotové verzie, z ktorých odhad vychádza, líšili od priemeru."
            : undefined
        }
      >
        {amountWords(estimate)}
      </div>
      <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
        Odhad podľa {tasksWords(estimate.tasks)} v pláne verzie, z{" "}
        {fromVersionsWords(estimate.samples)} — {pool}.
      </p>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs text-[var(--color-text-secondary)]">
        <dt>Kód a skúšky</dt>
        <dd>
          ≈ {tokens(estimate.code_tokens)} tokenov ×{" "}
          {formatEur(estimate.rate_code)} za 1 000 ={" "}
          {formatEur(estimate.code_eur)}
        </dd>
        <dt>Dokumentácia</dt>
        <dd>
          ≈ {tokens(estimate.docs_tokens)} tokenov ×{" "}
          {formatEur(estimate.rate_docs)} za 1 000 ={" "}
          {formatEur(estimate.docs_eur)}
        </dd>
      </dl>
      {!estimate.enough && (
        <p className="mt-3 text-xs text-[var(--color-state-warning-fg)]">
          Opiera sa len o {onVersionsWords(estimate.samples)}, preto je hrubý —
          spresní sa s každou ďalšou hotovou verziou.
        </p>
      )}
      <p className="mt-3 text-[11px] text-[var(--color-text-muted)]">
        Nie je to faktúra: účtuje sa podľa súpisu dodávky, keď je verzia dodaná.
      </p>
    </section>
  );
}

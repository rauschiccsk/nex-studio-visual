// AppliedWithoutQuestion — the Auditor's points the Staviteľ works in without a card (DEV-487).
//
// Director 21.11.2026, on 13 cards made of 2 blocking findings and 11 notes: „podľa mňa príliš veľa problémov tam je.“
// A card now comes only where the Manažér must decide; a technical correction with one right answer the Staviteľ
// works in himself — and the Manažér still sees every one of them here, beside the cards and in the summary, so
// nothing the review found is lost. The Auditor checks them in its next review.

interface Props {
  applied?: string[] | null;
}

export default function AppliedWithoutQuestion({ applied }: Props) {
  if (!applied || applied.length === 0) return null;
  return (
    <section aria-label="Zapracujem bez otázky" className="mb-2 mt-2 text-xs">
      <h4 className="font-medium text-[var(--color-text-primary)]">Zapracujem bez otázky ({applied.length})</h4>
      <p className="text-[11px] text-[var(--color-text-muted)]">
        Technické opravy z previerky, ktoré Staviteľ zapracuje sám — Auditor ich pri ďalšej previerke overí.
      </p>
      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[var(--color-text-secondary)]">
        {applied.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </section>
  );
}

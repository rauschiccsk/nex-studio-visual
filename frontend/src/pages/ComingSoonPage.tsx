/**
 * ComingSoonPage — a lightweight "pripravuje sa" placeholder for v2.0.0 nav
 * destinations whose real pages land in a later Milestone (CR-V2-019).
 *
 * The FINAL sidebar (design §4.1) adds the per-customer deploy surfaces
 * — 👥 Zákazníci, 🧪 UAT, 🚀 PROD — now, but their pages are built in
 * Milestone G (Zákazníci = CR-V2-025, UAT/PROD = CR-V2-027). Until then their
 * routes resolve here so the nav items never 404 (per the disabled-over-hidden
 * convention: discoverable, just not yet active).
 *
 * Intentionally trivial — replaced wholesale by the real page in its owning CR.
 */

export interface ComingSoonPageProps {
  /** The Slovak title of the destination (e.g. "Zákazníci"). */
  title: string;
  /** Optional one-line description of what the page will do. */
  description?: string;
}

export default function ComingSoonPage({ title, description }: ComingSoonPageProps) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <div className="text-4xl" aria-hidden>
        🚧
      </div>
      <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">{title}</h1>
      <p className="max-w-md text-sm text-[var(--color-text-secondary)]">
        {description ?? "Táto sekcia sa pripravuje a bude dostupná v ďalšej verzii."}
      </p>
    </div>
  );
}

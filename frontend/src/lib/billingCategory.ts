// DEV-75 — what a task's delivered work is billed as (Director 12.10.2026): a fix of an error in code we delivered
// (a warranty fix), what the customer asked for, or a gift — an attention of ICC. Empty = the version's category.

export type BillingCategory = "bug" | "request" | "gift";

/** In the order of the statement's lines. */
export const BILLING_CATEGORIES: BillingCategory[] = ["request", "bug", "gift"];

export const CATEGORY_LABELS: Record<BillingCategory, string> = {
  request: "Požiadavka zákazníka",
  bug: "Oprava chyby",
  gift: "Dar — pozornosť ICC",
};

/** The category inside a sentence — „zľava pre dar 100 %“. */
export const CATEGORY_NAMES: Record<BillingCategory, string> = {
  request: "požiadavka zákazníka",
  bug: "oprava chyby",
  gift: "dar",
};

/** What a task without its own category counts as. */
export function defaultLabel(fallback: BillingCategory | null): string {
  return fallback ? `podľa verzie (${CATEGORY_NAMES[fallback]})` : "podľa verzie (druh práce neurčený)";
}

/** The one category all these share, `null` when they share none of their own, `"mixed"` when they differ. */
export function commonCategory(categories: (BillingCategory | null)[]): BillingCategory | null | "mixed" {
  const unique = new Set(categories);
  if (unique.size > 1) return "mixed";
  return categories[0] ?? null;
}

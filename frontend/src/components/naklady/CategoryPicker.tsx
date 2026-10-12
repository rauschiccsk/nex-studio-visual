// CategoryPicker — what a task is billed as, or every task of an epic or feat at once (DEV-75). „Podľa verzie“ = the
// task has no category of its own and counts as the version's (a fast fix of our error → a bug fix, else a customer
// request). Several tasks with different categories show „rôzne“ until one is picked for all.

import { BILLING_CATEGORIES, CATEGORY_LABELS, defaultLabel, type BillingCategory } from "@/lib/billingCategory";

const MIXED = "__mixed";

export default function CategoryPicker({
  value,
  fallback,
  label,
  disabled,
  quiet,
  onPick,
}: {
  value: BillingCategory | null | "mixed";
  /** The version's category — what „podľa verzie“ means. */
  fallback: BillingCategory | null;
  /** Read by a screen reader: whose category this is. */
  label: string;
  disabled?: boolean;
  /** Read as a note under a node of the task plan, not as a form field — it shows its frame only on hover. */
  quiet?: boolean;
  onPick: (category: BillingCategory | null) => void;
}) {
  return (
    <select
      aria-label={label}
      title={label}
      value={value === "mixed" ? MIXED : (value ?? "")}
      disabled={disabled}
      onChange={(e) => {
        if (e.target.value !== MIXED) onPick(e.target.value === "" ? null : (e.target.value as BillingCategory));
      }}
      className={`min-w-0 max-w-full rounded border disabled:opacity-50 pointer-coarse:min-h-11 ${
        quiet
          ? "border-transparent bg-transparent px-0.5 py-0 text-[10px] text-[var(--color-text-muted)] hover:border-[var(--color-border-default)]"
          : "border-[var(--color-border-default)] bg-[var(--color-surface)] px-1 py-0.5 text-[11px] text-[var(--color-text-secondary)]"
      }`}
    >
      {value === "mixed" && (
        <option value={MIXED} disabled>
          rôzne
        </option>
      )}
      <option value="">{defaultLabel(fallback)}</option>
      {BILLING_CATEGORIES.map((c) => (
        <option key={c} value={c}>
          {CATEGORY_LABELS[c]}
        </option>
      ))}
    </select>
  );
}

// StatementTasks — the version's tasks with what each is billed as (DEV-75), where the manager confirms or changes the
// category before issuing the statement: per task, or for a whole epic at once. The AI Agent proposed the category in
// the plan; a task without one counts as the version's. Each task says how many tokens of the delivery it wrote.

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import CategoryPicker from "@/components/naklady/CategoryPicker";
import { BILLING_CATEGORIES, CATEGORY_NAMES, commonCategory, type BillingCategory } from "@/lib/billingCategory";
import type { StatementTask } from "@/services/api/deliveryStatements";

const int = new Intl.NumberFormat("sk-SK");

function byEpic(tasks: StatementTask[]): { key: string; title: string; tasks: StatementTask[] }[] {
  const groups = new Map<string, { key: string; title: string; tasks: StatementTask[] }>();
  for (const t of tasks) {
    const key = t.label.split(".")[0] ?? t.label;
    const group = groups.get(key) ?? { key, title: t.epic_title, tasks: [] };
    group.tasks.push(t);
    groups.set(key, group);
  }
  return [...groups.values()];
}

export default function StatementTasks({
  tasks,
  fallback,
  busy,
  onSet,
}: {
  tasks: StatementTask[];
  /** The version's category — what a task without its own counts as. */
  fallback: BillingCategory | null;
  busy: boolean;
  onSet: (taskIds: string[], category: BillingCategory | null) => void;
}) {
  const [open, setOpen] = useState(false);
  if (tasks.length === 0) return null;
  const counts = BILLING_CATEGORIES.map((c) => [c, tasks.filter((t) => t.category === c).length] as const).filter(
    ([, n]) => n > 0,
  );
  return (
    <div className="mb-3 text-[11px]" data-testid="statement-tasks">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-left text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] pointer-coarse:min-h-11"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        Kategórie úloh: {tasks.length} {tasks.length === 1 ? "úloha" : tasks.length < 5 ? "úlohy" : "úloh"}
        {counts.length > 0 && ` — ${counts.map(([c, n]) => `${CATEGORY_NAMES[c]} ${n}`).join(", ")}`}
      </button>
      {open && (
        <div className="mt-1 space-y-2">
          {byEpic(tasks).map((epic) => (
            <div key={epic.key} className="rounded border border-[var(--color-border-default)] px-2 py-1.5">
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                <span className="min-w-0 font-medium text-[var(--color-text-primary)]">
                  {epic.key}. {epic.title}
                </span>
                <CategoryPicker
                  label={`Kategória všetkých úloh celku ${epic.key}`}
                  value={commonCategory(epic.tasks.map((t) => t.own_category))}
                  fallback={fallback}
                  disabled={busy}
                  onPick={(c) =>
                    onSet(
                      epic.tasks.map((t) => t.id),
                      c,
                    )
                  }
                />
              </div>
              <ul className="space-y-1">
                {epic.tasks.map((t) => (
                  <li key={t.id} className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
                    <span className="min-w-0 text-[var(--color-text-secondary)]">
                      <span className="font-mono">{t.label}</span> {t.title}
                      <span className="ml-1 text-[var(--color-text-muted)]">
                        · {t.tokens > 0 ? `${int.format(t.tokens)} tokenov` : "v dodávke nič nenapísala"}
                      </span>
                    </span>
                    <CategoryPicker
                      label={`Kategória úlohy ${t.label}`}
                      value={t.own_category}
                      fallback={fallback}
                      disabled={busy}
                      onPick={(c) => onSet([t.id], c)}
                    />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

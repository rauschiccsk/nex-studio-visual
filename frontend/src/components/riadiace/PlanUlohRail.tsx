// PlanUlohRail — the right rail of the Riadiace centrum (spine STEP 1). A PLACEHOLDER for now: the real
// "Plán úloh" (the task-plan tree) lands in step 3, which drops the real panel into this exact cell with zero
// grid churn. Wired to versionId already so the later drop-in of TaskPlanPanel needs no layout change.
//
// Deliberately does NOT wire the existing components/cockpit/TaskPlanPanel — that panel is coupled to the old
// Návrh / Programovanie phase split and is re-homed in a later step.

interface Props {
  versionId: string | null;
}

export function PlanUlohRail({ versionId }: Props) {
  return (
    <aside
      data-version-id={versionId ?? undefined}
      className="flex h-full min-h-0 flex-col border-l border-[var(--color-border-default)] bg-[var(--color-surface)]"
    >
      <div className="flex-shrink-0 border-b border-[var(--color-border-default)] px-4 py-2.5">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">Plán úloh</h2>
      </div>
      <div className="flex flex-1 items-center justify-center p-4 text-center">
        <p className="max-w-[15rem] text-xs text-[var(--color-text-muted)]">
          Plán sa objaví, keď sa dohodneme na špecifikácii.
        </p>
      </div>
    </aside>
  );
}

export default PlanUlohRail;

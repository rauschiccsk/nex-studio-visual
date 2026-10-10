// WorkKindChoice — „oprava chyby v dodanom kóde“ or „zmena alebo nová práca“, chosen when a version or a fast fix
// starts (DEV-50). It decides whether the delivered-token statement bills the work.

import { WORK_KIND_OPTIONS, type WorkKind } from "@/lib/workKind";

interface Props {
  name: string;
  value: WorkKind | null;
  onChange: (kind: WorkKind) => void;
  disabled?: boolean;
}

export default function WorkKindChoice({ name, value, onChange, disabled }: Props) {
  return (
    <fieldset className="space-y-1" disabled={disabled}>
      <legend className="mb-1 text-xs font-medium text-[var(--color-text-secondary)]">Druh práce</legend>
      {WORK_KIND_OPTIONS.map((o) => (
        <label key={o.value} className="flex cursor-pointer items-start gap-2 text-xs text-[var(--color-text-primary)]">
          <input
            type="radio"
            name={name}
            value={o.value}
            checked={value === o.value}
            onChange={() => onChange(o.value)}
            className="mt-0.5"
          />
          <span>
            {o.label} <span className="text-[var(--color-text-muted)]">— {o.hint}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

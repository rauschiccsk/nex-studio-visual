// DEV-395: the languages an app on the template speaks — the main one and the others. One control for the new-project
// form and the project settings; the value is the list the backend keeps, the main language first.
import { withMain, type LanguageOption } from "@/lib/appLanguages";

const APP_LANGUAGES_HINT =
  "Každý človek si v aplikácii vyberie svoj jazyk sám. Kým nevyberie, aplikácia hovorí jazykom inštalácie, ktorý " +
  "určíš pri zákazníkovi, inak hlavným jazykom.";

interface Props {
  options: LanguageOption[];
  value: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
}

export function AppLanguagesChoice({ options, value, onChange, disabled = false }: Props) {
  const main = value[0] ?? options[0]?.code ?? "";
  const others = value.slice(1);
  return (
    <div className="space-y-2" data-testid="app-languages">
      <label className="flex items-center gap-3 text-sm text-[var(--color-text-primary)]">
        <span className="w-32 shrink-0 text-[var(--color-text-secondary)]">Hlavný jazyk</span>
        <select
          value={main}
          disabled={disabled}
          onChange={(e) => onChange(withMain(options, e.target.value, others))}
          className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] disabled:opacity-50"
        >
          {options.map((o) => (
            <option key={o.code} value={o.code}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      {options.length > 1 && (
        <div className="flex items-start gap-3 text-sm">
          <span className="w-32 shrink-0 pt-0.5 text-[var(--color-text-secondary)]">Ďalšie jazyky</span>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {options
              .filter((o) => o.code !== main)
              .map((o) => (
                <label key={o.code} className="flex items-center gap-2 text-[var(--color-text-primary)] cursor-pointer">
                  <input
                    type="checkbox"
                    checked={others.includes(o.code)}
                    disabled={disabled}
                    onChange={(e) =>
                      onChange(
                        withMain(options, main, e.target.checked ? [...others, o.code] : others.filter((c) => c !== o.code)),
                      )
                    }
                    className="w-4 h-4 rounded border-[var(--color-border-default)] bg-[var(--color-canvas)] text-primary-500 focus:ring-primary-500"
                  />
                  {o.label}
                </label>
              ))}
          </div>
        </div>
      )}
      <p className="text-xs text-[var(--color-text-muted)]">{APP_LANGUAGES_HINT}</p>
    </div>
  );
}

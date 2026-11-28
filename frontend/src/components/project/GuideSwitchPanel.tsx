/**
 * Sprievodca v aplikácii (DEV-536) — whether the project's app has Sprievodca („Ako urobiť“ and the guiding step by step,
 * nex-app-kit 0.24.0+), and the switch that turns it off for a special app. On is the default; the app's manifest keeps
 * it and the switch commits it. The Director 28.11.2026: „Má byť default, že tam je ale v prípade, že ide o nejakú
 * špeciálnu aplikáciu aby sme to mohli vypnúť.“
 */
import { useEffect, useState } from "react";
import { Compass, Loader2 } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { getGuideApi, setGuideApi, type GuideSwitch } from "@/services/api/projectGuide";

interface Props {
  projectId: string;
  /** The owner or the admin account may switch it; anyone else reads it. */
  canEdit: boolean;
}

export default function GuideSwitchPanel({ projectId, canEdit }: Props) {
  const [guide, setGuide] = useState<GuideSwitch | null>(null);
  const [busy, setBusy] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<HumanError | null>(null);

  useEffect(() => {
    let cancelled = false;
    getGuideApi(projectId)
      .then((found) => !cancelled && setGuide(found))
      .catch((exc) => !cancelled && setError(humanizeApiError(exc, "Stav Sprievodcu sa nepodarilo načítať")));
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const turn = async (on: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const done = await setGuideApi(projectId, on);
      setGuide(done);
      setWarnings(done.warnings ?? []);
    } catch (exc) {
      setError(humanizeApiError(exc, on ? "Sprievodcu sa nepodarilo zapnúť" : "Sprievodcu sa nepodarilo vypnúť"));
    } finally {
      setBusy(false);
    }
  };

  if (guide && !guide.applies) return null;
  if (!guide && !error) return null;
  return (
    <section
      aria-label="Sprievodca v aplikácii"
      className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)]">
        <Compass className="h-3.5 w-3.5" /> Sprievodca v aplikácii
      </h2>
      {guide && !guide.refusal && guide.on && (
        <p className="mt-2 text-sm text-[var(--color-text-primary)]">
          <span className="font-semibold">Zapnutý.</span> Aplikácia má „Ako urobiť“: človek napíše, čo chce urobiť, a
          Sprievodca ho krok za krokom dovedie k cieľu — kliká vždy človek. Kroky pripravuje Staviteľ a kokpit pred
          Overením skontroluje, že žiadna obrazovka neostala bez nich.
        </p>
      )}
      {guide && !guide.refusal && !guide.on && (
        <p className="mt-2 text-sm text-[var(--color-text-primary)]">
          <span className="font-semibold">Vypnutý.</span> Aplikácia má „Ako začať“ s písanou príručkou. Sprievodca sa
          vypína len pri špeciálnej aplikácii.
        </p>
      )}
      {guide?.refusal && <p className="mt-2 text-sm text-[var(--color-text-secondary)]">{guide.refusal}</p>}
      {guide && !guide.refusal && canEdit && (
        <button
          type="button"
          onClick={() => void turn(!guide.on)}
          disabled={busy}
          className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-4 py-2 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50"
        >
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {guide.on ? "Vypnúť Sprievodcu" : "Zapnúť Sprievodcu"}
        </button>
      )}
      {warnings.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-sm text-[var(--color-state-warning-fg)]">
          {warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      <ErrorNote error={error} className="mt-2" />
    </section>
  );
}

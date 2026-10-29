import { useEffect, useState } from "react";
import {
  convertToAppTemplateApi,
  getAppTemplateConversionApi,
  type AppTemplateConversionOffer,
} from "@/services/api/projects";
import ErrorNote from "@/components/common/ErrorNote";
import { humanizeApiError, type HumanError } from "@/services/apiError";

/**
 * DEV-254: put a project built without a template onto the app template, when a new version is founded. Director
 * 29.10.2026: „Začneme s projektom nex-knowledge“. The cockpit adds the manifest and nex-app-kit in one commit; the
 * project's screens stay and the version being founded moves them into the frame. Offered greyed out with the reason
 * when the cockpit cannot do it now (a version in progress, an installation whose people would have to move).
 */
export function AppTemplateConversionPrompt({
  projectId,
  onConverted,
}: {
  projectId: string;
  onConverted: (template: string, kitVersion: string) => void;
}) {
  const [offer, setOffer] = useState<AppTemplateConversionOffer | null>(null);
  const [ai, setAi] = useState(false);
  const [busy, setBusy] = useState(false);
  const [later, setLater] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAppTemplateConversionApi(projectId)
      .then((o) => { if (!cancelled) setOffer(o); })
      .catch(() => {}); // best-effort, like the upgrade prompts — no answer, no offer
    return () => { cancelled = true; };
  }, [projectId]);

  const template = offer?.templates?.find((t) => t.id === offer.default_template);
  if (!offer || !template || later) return null;

  async function convert() {
    setBusy(true);
    setError(null);
    try {
      const res = await convertToAppTemplateApi(projectId, template!.id, ai);
      onConverted(res.template, res.kit_version);
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Prevod na šablónu aplikácie zlyhal"));
    } finally {
      setBusy(false);
    }
  }

  const disabled = !offer.available || busy;
  return (
    <div className="rounded-lg border border-[var(--color-accent-primary)]/40 bg-[var(--color-surface)] overflow-hidden">
      <div className="px-4 pt-3.5 pb-3 border-b border-[var(--color-border-default)]">
        <span className="inline-block font-mono text-[10px] tracking-wider uppercase px-2 py-0.5 rounded-full border border-[var(--color-accent-primary)]/40 text-[var(--color-accent-primary)]">
          nex-app-kit
        </span>
        <h3 className="mt-2 text-[15px] font-semibold text-[var(--color-text-primary)]">
          Previesť projekt na šablónu aplikácie
        </h3>
        <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
          Projekt má zatiaľ vlastný ľavý panel, prihlásenie a Nastavenia. Na šablóne „{template.label}“ ich dostane
          z jednotného rámu: {template.description}
        </p>
      </div>
      <div className="px-4 py-3.5 space-y-3">
        <label
          className={`flex items-start gap-3 text-sm ${
            disabled ? "text-[var(--color-text-muted)] cursor-not-allowed" : "text-[var(--color-text-primary)] cursor-pointer"
          }`}
        >
          <input
            type="checkbox"
            checked={ai}
            disabled={disabled}
            onChange={(e) => setAi(e.target.checked)}
            className="mt-0.5 w-4 h-4 rounded border-[var(--color-border-default)] bg-[var(--color-canvas)] text-primary-500 focus:ring-primary-500 disabled:opacity-50"
          />
          <span>
            Aplikácia používa AI agentov
            <span className="block text-xs text-[var(--color-text-muted)]">
              V Nastaveniach pribudne záložka Agenti — model a úsilie pre každú rolu agenta.
            </span>
          </span>
        </label>
        <p className="text-[12px] text-[var(--color-text-muted)] border-l-2 border-[var(--color-status-success)] pl-2.5">
          Kokpit pridá šablónu jedným zápisom a obrazovky projektu nechá. Do rámu ich presunie agent vo verzii, ktorú
          teraz zakladáš — kokpit pred Overením skontroluje, že je rám celý.
        </p>
        {!offer.available && offer.reason && (
          <p className="text-[12px] text-[var(--color-text-secondary)]">Teraz sa nedá: {offer.reason}</p>
        )}
        <ErrorNote
          error={error}
          className="rounded-lg bg-[var(--color-state-error-bg)] border border-[var(--color-state-error-bg)] p-3"
        />
      </div>
      <div className="flex gap-2.5 px-4 pb-4">
        <button
          type="button"
          disabled={busy}
          onClick={() => setLater(true)}
          className="flex-1 text-[13px] font-medium py-2 rounded-md border border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-60"
        >
          Teraz nie
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => void convert()}
          className="flex-1 text-[13px] font-semibold py-2 rounded-md bg-[var(--color-accent-primary)] text-white hover:opacity-90 disabled:opacity-60"
        >
          {busy ? "Prevádzam…" : `Previesť na ${template.label}`}
        </button>
      </div>
    </div>
  );
}

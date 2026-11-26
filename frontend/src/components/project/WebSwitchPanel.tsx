/**
 * Frontendy projektu (DEV-522) — the frontends the project HAS, as a fact; and „Pridať webový frontend“ for the admin
 * account only.
 *
 * What the Manažér sets when founding a project binds it: the type, the sign-in and the frontends do not change while
 * it is made. 26.11.2026 orthodox-register: this panel offered „Prepnúť na Web“ in a box titled „Projekt s webom“ that
 * listed a web frontend with its port, and the Director read it as the project's setting. The Director: „Je
 * neprípustné, aby typ projektu a ďaľšie jeho parametre, ktoré určíme pri zakladaní projektu boli zmenené počas tvorby
 * projektu.“ The one exception — adding the web frontend (DEV-164) — is for the admin account, before the project's
 * first PROD deploy, with a written reason that is kept: „Spravme to tak, aby kopit aj do budúcna mal takú funkciu -
 * pridať webový frontend.“
 */
import { useEffect, useState } from "react";
import { Globe, Loader2 } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { getWebSwitchApi, switchToWebApi, type WebSwitch } from "@/services/api/projects";
import { isAdminAccount } from "@/services/permissions";
import { useAuthStore } from "@/store/authStore";

const LOGIN: Record<string, string> = {
  username: "prihlásenie menom a heslom",
  email: "prihlásenie e-mailom a heslom, registrácia",
  token: "spúšťa ho NEX Manager",
};

/** The frontends — each with its folder, sign-in and port. */
function Frontends({ surfaces }: { surfaces: WebSwitch["surfaces"] }) {
  return (
    <ul className="mt-2 space-y-1 text-sm text-[var(--color-text-primary)]">
      {surfaces.map((surface) => (
        <li key={surface.name}>
          <span className="font-semibold">{surface.label}</span> — priečinok{" "}
          <span className="font-mono">{surface.directory}/</span>, {LOGIN[surface.login] ?? surface.login}
          {surface.port != null && <>, port {surface.port}</>}
        </li>
      ))}
    </ul>
  );
}

interface Props {
  projectId: string;
  /** The project is Web already — it has both frontends; nothing is offered. */
  isWeb: boolean;
  /** Called after the web frontend was added — the page reads the project again. */
  onSwitched: () => void;
}

export default function WebSwitchPanel({ projectId, isWeb, onSwitched }: Props) {
  const admin = isAdminAccount(useAuthStore((s) => s.user));
  const [preview, setPreview] = useState<WebSwitch | null>(null);
  const [reason, setReason] = useState("");
  const [adding, setAdding] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<HumanError | null>(null);

  useEffect(() => {
    let cancelled = false;
    getWebSwitchApi(projectId)
      .then((found) => !cancelled && setPreview(found))
      .catch((exc) => !cancelled && setError(humanizeApiError(exc, "Frontendy projektu sa nepodarilo načítať")));
    return () => {
      cancelled = true;
    };
  }, [projectId, isWeb]);

  const addWeb = async () => {
    setAdding(true);
    setError(null);
    try {
      const done = await switchToWebApi(projectId, reason.trim());
      setWarnings(done.warnings ?? []);
      setReason("");
      onSwitched();
    } catch (exc) {
      setError(humanizeApiError(exc, "Webový frontend sa nepodarilo pridať"));
    } finally {
      setAdding(false);
    }
  };

  if (!preview && !error) return null;
  return (
    <section
      aria-label="Frontendy projektu"
      className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)]">
        <Globe className="h-3.5 w-3.5" /> Frontendy projektu
      </h2>
      {preview && <Frontends surfaces={preview.surfaces} />}
      {preview && admin && !isWeb && !preview.allowed && (
        <p className="mt-2 text-sm text-[var(--color-text-secondary)]">
          Webový frontend sa pridať nedá: {preview.refusal}
        </p>
      )}
      {preview?.allowed && admin && !isWeb && (
        <div className="mt-3 rounded-lg border border-[var(--color-border-default)] p-3">
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Pridať webový frontend</h3>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            Čo sa nastaví pri založení projektu, sa počas tvorby nemení. Webový frontend je jediná výnimka: pridáva ho
            len účet admin, kým projekt nebol nasadený do PROD, a dôvod sa zapíše. Projekt potom bude mať:
          </p>
          <Frontends surfaces={preview.after} />
          <label className="mt-3 block text-sm text-[var(--color-text-secondary)]">
            Dôvod
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              spellCheck={true}
              lang="sk"
              data-no-draft="krátky dôvod jedného kliku — zapíše sa hneď so zmenou projektu"
              className="mt-1 block w-full max-w-xl rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-3 py-2 text-sm text-[var(--color-text-primary)]"
            />
          </label>
          <button
            type="button"
            onClick={() => void addWeb()}
            disabled={adding || !reason.trim()}
            className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-2 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-50"
          >
            {adding && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Pridať webový frontend
          </button>
        </div>
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

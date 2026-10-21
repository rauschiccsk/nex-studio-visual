/**
 * Prepnúť na Web (DEV-164) — a Štandardný project becomes a Web project: the administration it has (frontend/, sign-in
 * by name) and a public web (web/, sign-in by e-mail with registration), both on its one backend.
 *
 * Director 20.10.2026: „Do web FE bude sa prihlasovať pomocou emailu a hesla, a do admin FE len meno a heslo. Ideme
 * kokpit naučiť stavať projekt s dvoma frontendmi.“ The panel first asks the cockpit what the switch would give — the
 * two frontends and the web's port — or the one reason it cannot; the switch itself takes a second, deliberate click.
 * It is shown to whoever may operate the project; a Web project sees its two frontends here, and right after a switch
 * also what finished only partly.
 */
import { useEffect, useState } from "react";
import { Globe, Loader2 } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { getWebSwitchApi, switchToWebApi, type WebSwitch } from "@/services/api/projects";

const LOGIN: Record<string, string> = {
  username: "prihlásenie menom a heslom",
  email: "prihlásenie e-mailom a heslom, registrácia",
  token: "spúšťa ho NEX Manager",
};

/** The project's frontends — each with its folder, sign-in and port. */
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
  /** The project is Web already — the panel names its frontends instead of offering the switch. */
  isWeb: boolean;
  /** Called after a switch that went through — the page reads the project again. */
  onSwitched: () => void;
}

export default function WebSwitchPanel({ projectId, isWeb, onSwitched }: Props) {
  const [preview, setPreview] = useState<WebSwitch | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<HumanError | null>(null);

  useEffect(() => {
    let cancelled = false;
    getWebSwitchApi(projectId)
      .then((found) => !cancelled && setPreview(found))
      .catch((exc) => !cancelled && setError(humanizeApiError(exc, "Či sa projekt dá prepnúť na Web, sa nepodarilo zistiť")));
    return () => {
      cancelled = true;
    };
  }, [projectId, isWeb]);

  const doSwitch = async () => {
    setSwitching(true);
    setError(null);
    try {
      const done = await switchToWebApi(projectId);
      setWarnings(done.warnings ?? []);
      onSwitched();
    } catch (exc) {
      setError(humanizeApiError(exc, "Projekt sa nepodarilo prepnúť na Web"));
      setConfirming(false);
    } finally {
      setSwitching(false);
    }
  };

  if (!preview && !error) return null;
  return (
    <section
      aria-label="Prepnúť na Web"
      className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)]">
        <Globe className="h-3.5 w-3.5" /> Projekt s webom
      </h2>
      {preview && isWeb && (
        <Frontends surfaces={preview.surfaces} />
      )}
      {preview && !isWeb && !preview.allowed && (
        <p className="mt-2 text-sm text-[var(--color-text-secondary)]">
          Na Web sa tento projekt prepnúť nedá: {preview.refusal}
        </p>
      )}
      {preview?.allowed && !isWeb && (
        <>
          <p className="mt-2 text-sm text-[var(--color-text-secondary)]">
            Projekt môže mať okrem administrácie aj verejný web — oba na tom istom backende:
          </p>
          <Frontends surfaces={preview.surfaces} />
          {!confirming ? (
            <button
              type="button"
              onClick={() => setConfirming(true)}
              className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-4 py-2 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)]"
            >
              Prepnúť na Web
            </button>
          ) : (
            <div className="mt-3 rounded-lg border border-[var(--color-state-warning-fg)]/40 bg-[var(--color-state-warning-bg)] p-3 text-sm">
              <p className="text-[var(--color-state-warning-fg)]">
                Do priečinka projektu pribudne web zo šablóny, jeho automatické kontroly a kontrola pred uložením —
                uložia sa a odošlú na GitHub. Prepnutie sa nedá vrátiť tlačidlom.
              </p>
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  onClick={() => void doSwitch()}
                  disabled={switching}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-2 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-50"
                >
                  {switching && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  Áno, prepnúť na Web
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(false)}
                  disabled={switching}
                  className="rounded-lg border border-[var(--color-border-default)] px-4 py-2 text-xs font-medium text-[var(--color-text-secondary)] disabled:opacity-50"
                >
                  Zrušiť
                </button>
              </div>
            </div>
          )}
        </>
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

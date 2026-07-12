import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  CheckCircle2,
  ExternalLink,
  FolderOpen,
  Loader2,
  Lock,
  RefreshCw,
  UploadCloud,
} from "lucide-react";

import { acceptCustomerUat, deployCustomer, getDeployMatrix } from "@/services/api/deploy";
import { ApiError } from "@/services/api";
import { humanizeApiError } from "@/services/apiError";
import { useActiveContextStore } from "@/store/activeContextStore";
import type { DeployEnvironment, DeployMatrix, DeployMatrixRow, DeployResult } from "@/types/deploy";

/**
 * Shared version × customer matrix page for the UAT and PROD tabs (CR-V2-027,
 * design §3.3/§3.4/§3.5). One component drives both environments — the design's
 * "one code path, no internal/external branch" principle applied to the two
 * deploy surfaces, so the matrix, the Nasadiť dropdown, the empty/loading states
 * and the no-project guard never drift between them.
 *
 * Per-environment behaviour (`environment` prop):
 *   - **uat**  — per-customer link to the live UAT URL + an "Akceptovať" action
 *     that records who/when/version/customer and opens PROD for that pair (§3.5).
 *   - **prod** — Nasadiť is DISABLED until that (version, customer) UAT has been
 *     accepted (`accepted_versions`); the never-bypassed acceptance gate (§3.5,
 *     incident 2026-06-10). The backend enforces it too — the disabled control
 *     just stops a doomed submit.
 *
 * Different customers may run different versions simultaneously (§3.3): each row
 * carries its own currently-deployed version.
 *
 * Secret handling (CLAUDE.md §4/§5, OQ-5): nothing here reads or shows secret
 * material — secrets live only in the backend credentials store.
 */
export interface DeployMatrixPageProps {
  environment: DeployEnvironment;
}

const LABELS: Record<DeployEnvironment, { title: string; intro: string; column: string }> = {
  uat: {
    title: "UAT",
    intro:
      "Per-zákazník testovacie nasadenie. Nasaď overenú verziu, otestuj ju na UAT URL a klikni Akceptovať — tým sa otvorí PROD pre danú verziu.",
    column: "Verzia na UAT",
  },
  prod: {
    title: "PROD",
    intro:
      "Per-zákazník produkčné nasadenie. PROD je možné nasadiť až po akceptácii UAT danej verzie — bez akceptácie je Nasadiť zablokované.",
    column: "Verzia v PROD",
  },
};

// Normalise a version_number for DISPLAY (audit obs #3): some are stored "v1.0.0" (the graduated first-PROD)
// and some "1.1.0" — strip a leading "v" so UAT + PROD always read the same bare-semver form. The STORED value
// (the deploy identifier used in requests / accepted_versions) is never touched — this is display-only.
const fmtVer = (v: string | null | undefined): string => (v ?? "").replace(/^v/i, "");

export default function DeployMatrixPage({ environment }: DeployMatrixPageProps) {
  const navigate = useNavigate();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const slug = selectedProject?.slug;
  const labels = LABELS[environment];

  const [matrix, setMatrix] = useState<DeployMatrix | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Per-customer chosen version in the Nasadiť dropdown (keyed by customer_id).
  const [picked, setPicked] = useState<Record<string, string>>({});
  // The customer_id currently mid-action (deploy/accept) → disables its row.
  const [busy, setBusy] = useState<string | null>(null);
  const [rowError, setRowError] = useState<Record<string, string>>({});
  // Audit Theme 4: the successful DeployResult per customer (url / bumped_to / warnings) — previously thrown
  // away, so the manager got no confirmation, no live link, no version-bump notice.
  const [rowResult, setRowResult] = useState<Record<string, DeployResult>>({});

  const load = useCallback(() => {
    if (!slug) return;
    setLoading(true);
    setLoadError(null);
    getDeployMatrix(slug)
      .then(setMatrix)
      .catch((err) => {
        if (err instanceof ApiError) {
          setLoadError(humanizeApiError(err, "Načítanie zlyhalo").message);
        } else {
          setLoadError("Sieťová chyba pri načítavaní matice nasadení.");
        }
      })
      .finally(() => setLoading(false));
  }, [slug]);

  useEffect(() => {
    load();
  }, [load]);

  function setRowMsg(customerId: string, msg: string | null) {
    setRowError((prev) => {
      const next = { ...prev };
      if (msg === null) delete next[customerId];
      else next[customerId] = msg;
      return next;
    });
  }

  /** The version a row's Nasadiť will deploy: the explicit pick, else newest verified. */
  function pickedVersion(row: DeployMatrixRow): string | undefined {
    return picked[row.customer_id] ?? matrix?.verified_versions[0];
  }

  /**
   * The PROD acceptance gate (§3.5): for PROD, a version may be deployed ONLY if
   * the customer has accepted it. UAT has no such gate. Returns the reason a
   * deploy is blocked, or null when it is allowed.
   */
  function deployBlockedReason(row: DeployMatrixRow): string | null {
    const version = pickedVersion(row);
    if (!version) return "Žiadna overená verzia na nasadenie.";
    if (environment === "prod" && !row.accepted_versions.includes(version)) {
      return `PROD je zablokované: verzia ${fmtVer(version)} nemá akceptované UAT pre tohto zákazníka.`;
    }
    return null;
  }

  async function handleDeploy(row: DeployMatrixRow) {
    const version = pickedVersion(row);
    if (!version) return;
    setRowMsg(row.customer_id, null);
    setRowResult((prev) => {
      const next = { ...prev };
      delete next[row.customer_id];
      return next;
    });
    setBusy(row.customer_id);
    try {
      const result = await deployCustomer(row.customer_id, { version_number: version, environment });
      setRowResult((prev) => ({ ...prev, [row.customer_id]: result }));
      load();
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          // The backend gate (never bypassed) rejected, e.g. PROD without acceptance. Show the specific
          // plain-Slovak acceptance-gate cause — never the raw English backend `err.message`.
          setRowMsg(row.customer_id, "Nasadenie zablokované (akceptačná brána).");
        } else if (err.status === 403) {
          setRowMsg(row.customer_id, "Nasadenie je dostupné len pre rolu Manažér.");
        } else {
          setRowMsg(row.customer_id, humanizeApiError(err, "Nasadenie zlyhalo").message);
        }
      } else {
        setRowMsg(row.customer_id, "Sieťová chyba pri nasadení.");
      }
    } finally {
      setBusy(null);
    }
  }

  async function handleAccept(row: DeployMatrixRow) {
    if (!row.uat_version) return;
    if (
      !window.confirm(
        `Akceptovať UAT verziu ${fmtVer(row.uat_version)} pre zákazníka ${row.customer_name}? ` +
          "Otvorí sa tým PROD nasadenie pre túto verziu.",
      )
    )
      return;
    setRowMsg(row.customer_id, null);
    setBusy(row.customer_id);
    try {
      await acceptCustomerUat(row.customer_id, { version_number: row.uat_version });
      load();
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 403) {
          setRowMsg(row.customer_id, "Akceptácia je dostupná len pre rolu Manažér.");
        } else {
          setRowMsg(row.customer_id, humanizeApiError(err, "Akceptácia zlyhala").message);
        }
      } else {
        setRowMsg(row.customer_id, "Sieťová chyba pri akceptácii.");
      }
    } finally {
      setBusy(null);
    }
  }

  // No project pinned — project-scoped page, mirror the Zákazníci empty state.
  if (!selectedProject) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
        <FolderOpen className="h-10 w-10 text-[var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Nemáš vybraný projekt</h2>
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          {labels.title} je viazané na projekt. Otvor <span className="font-mono">Projekty</span> a pripni projekt.
        </p>
        <button
          onClick={() => navigate("/projects")}
          className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
        >
          Otvoriť Projekty
        </button>
      </div>
    );
  }

  const verified = matrix?.verified_versions ?? [];
  const rows = matrix?.rows ?? [];
  const currentCol = environment === "uat" ? (r: DeployMatrixRow) => r.uat_version : (r: DeployMatrixRow) => r.prod_version;

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-base font-bold text-[var(--color-text-primary)]">{labels.title}</h1>
        <button
          onClick={load}
          title="Obnoviť"
          className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
        >
          <RefreshCw className="h-3.5 w-3.5" /> Obnoviť
        </button>
      </div>
      <p className="mb-4 text-xs text-[var(--color-text-muted)]">
        Projekt <span className="text-[var(--color-text-secondary)]">{selectedProject.name}</span>. {labels.intro}
      </p>

      {loading ? (
        <div className="flex items-center gap-2 py-12 text-sm text-[var(--color-text-muted)]">
          <Loader2 className="h-4 w-4 animate-spin" /> Načítavam…
        </div>
      ) : loadError ? (
        <div className="rounded-lg bg-[var(--color-state-error-bg)] px-3 py-2 text-sm text-[var(--color-state-error-fg)]">
          {loadError}
        </div>
      ) : rows.length === 0 ? (
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-6 text-center text-sm text-[var(--color-text-muted)]">
          Zatiaľ žiadni zákazníci. Pridaj zákazníka v <span className="font-mono">Zákazníci</span>, potom sem nasadíš
          verziu.
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)]">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-[var(--color-border-default)] text-xs text-[var(--color-text-muted)]">
                <th className="px-3 py-2 font-medium">Zákazník</th>
                <th className="px-3 py-2 font-medium">{labels.column}</th>
                <th className="px-3 py-2 font-medium">Nasadiť verziu</th>
                <th className="px-3 py-2 font-medium text-right">Akcie</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border-default)]">
              {rows.map((row) => {
                const isBusy = busy === row.customer_id;
                const blocked = deployBlockedReason(row);
                const current = currentCol(row);
                const chosen = pickedVersion(row);
                // Env-generic live link (UAT or PROD), the last successful deploy result, and the UAT-accepted flag.
                const liveUrl = environment === "uat" ? row.uat_url : row.prod_url;
                // Audit #5: the newest attempt failed → flag it so the last-good/empty cell isn't read as green.
                const lastAttemptFailed =
                  environment === "uat" ? row.uat_last_attempt_failed : row.prod_last_attempt_failed;
                const result = rowResult[row.customer_id];
                const accepted =
                  environment === "uat" && !!row.uat_version && row.accepted_versions.includes(row.uat_version);
                return (
                  <tr key={row.customer_id} className="align-top">
                    {/* Customer */}
                    <td className="px-3 py-3">
                      <div className="font-medium text-[var(--color-text-primary)]">{row.customer_name}</div>
                      <div className="font-mono text-[11px] text-[var(--color-text-muted)]">{row.customer_slug}</div>
                    </td>

                    {/* Currently deployed version in this environment */}
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-1.5">
                        {current ? (
                          <span className="rounded bg-[var(--color-surface)] px-1.5 py-0.5 font-mono text-xs text-[var(--color-text-primary)]">
                            {fmtVer(current)}
                          </span>
                        ) : (
                          <span className="text-xs text-[var(--color-text-muted)]">—</span>
                        )}
                        {accepted && (
                          <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                            Akceptované ✓
                          </span>
                        )}
                        {lastAttemptFailed && (
                          <span
                            title="Najnovší pokus o nasadenie zlyhal — beží stále predošlá verzia (alebo žiadna). Skús nasadiť znova."
                            className="rounded-full border border-red-500/40 bg-red-500/10 px-1.5 py-0.5 text-[10px] font-medium text-red-600 dark:text-red-400"
                          >
                            Posledný pokus zlyhal
                          </span>
                        )}
                      </div>
                      {liveUrl && (
                        <a
                          href={liveUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-1 flex items-center gap-1 text-[11px] text-primary-500 hover:underline"
                        >
                          <ExternalLink className="h-3 w-3" /> Otvoriť aplikáciu
                        </a>
                      )}
                    </td>

                    {/* Nasadiť version picker (verified versions only) */}
                    <td className="px-3 py-3">
                      {verified.length === 0 ? (
                        <span className="text-xs text-[var(--color-text-muted)]">žiadna overená verzia</span>
                      ) : (
                        <select
                          value={chosen}
                          disabled={isBusy}
                          onChange={(e) =>
                            setPicked((prev) => ({ ...prev, [row.customer_id]: e.target.value }))
                          }
                          className="rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1 font-mono text-xs text-[var(--color-text-primary)]"
                        >
                          {verified.map((v) => (
                            <option key={v} value={v}>
                              {fmtVer(v)}
                            </option>
                          ))}
                        </select>
                      )}
                      {isBusy && (
                        <div className="mt-1 flex items-center gap-1 text-[11px] text-[var(--color-text-muted)]">
                          <Loader2 className="h-3 w-3 animate-spin" /> Nasadzujem… (~2 min, počkaj)
                        </div>
                      )}
                      {rowError[row.customer_id] && (
                        <div className="mt-1 text-[11px] text-[var(--color-state-error-fg)]">
                          {rowError[row.customer_id]}
                        </div>
                      )}
                      {result && !isBusy && (
                        <div className="mt-1 space-y-0.5 text-[11px] text-emerald-600 dark:text-emerald-400">
                          <div>✓ Nasadené{result.bumped_to ? ` — projekt povýšený na ${fmtVer(result.bumped_to)}` : ""}</div>
                          {result.url && (
                            <a
                              href={result.url}
                              target="_blank"
                              rel="noreferrer"
                              className="flex items-center gap-1 hover:underline"
                            >
                              <ExternalLink className="h-3 w-3" /> Otvoriť aplikáciu
                            </a>
                          )}
                          {result.warnings.map((w, i) => (
                            <div key={i} className="text-amber-600 dark:text-amber-400">
                              ⚠ {w}
                            </div>
                          ))}
                        </div>
                      )}
                    </td>

                    {/* Actions: Nasadiť (+ Akceptovať on UAT) */}
                    <td className="px-3 py-3">
                      <div className="flex items-center justify-end gap-2">
                        {environment === "uat" && (
                          <button
                            onClick={() => handleAccept(row)}
                            disabled={isBusy || !row.uat_version}
                            title={
                              row.uat_version
                                ? `Akceptovať UAT verziu ${fmtVer(row.uat_version)}`
                                : "Najprv nasaď verziu na UAT"
                            }
                            className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] disabled:opacity-40"
                          >
                            <CheckCircle2 className="h-3.5 w-3.5" /> Akceptovať
                          </button>
                        )}
                        <button
                          onClick={() => handleDeploy(row)}
                          disabled={isBusy || verified.length === 0 || blocked !== null}
                          title={blocked ?? `Nasadiť verziu ${fmtVer(chosen)} do ${labels.title}`}
                          className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-40"
                        >
                          {isBusy ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : blocked && environment === "prod" ? (
                            <Lock className="h-3.5 w-3.5" />
                          ) : (
                            <UploadCloud className="h-3.5 w-3.5" />
                          )}
                          Nasadiť
                        </button>
                      </div>
                      {environment === "prod" && blocked && (
                        <div className="mt-1 text-right text-[11px] text-[var(--color-text-muted)]">
                          čaká na akceptáciu UAT
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

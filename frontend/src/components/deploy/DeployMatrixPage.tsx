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

import { acceptCustomerUat, deployCustomer, getDeployMatrix, uatLaunch } from "@/services/api/deploy";
import { ApiError } from "@/services/api";
import { humanizeApiError } from "@/services/apiError";
import { useActiveContextStore } from "@/store/activeContextStore";
import type { DeployEnvironment, DeployMatrix, DeployMatrixRow, DeployResult } from "@/types/deploy";
import DeployBlockNotice from "./DeployBlockNotice";
import { fmtVer } from "./version";

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

// ``introPaused`` (v4.0.55): while nothing is deployable, the normal intro instructs a sequence the manager
// CANNOT start — and it sat directly above the notice explaining why. Telling someone to do the impossible
// is the same defect as the silent grey button, one line higher up.
const LABELS: Record<DeployEnvironment, { title: string; intro: string; introPaused: string; column: string }> =
  {
    uat: {
      title: "UAT",
      intro:
        "Per-zákazník testovacie nasadenie. Nasaď overenú verziu, otestuj ju na UAT URL a klikni Akceptovať — tým sa otvorí PROD pre danú verziu.",
      introPaused: "Per-zákazník testovacie nasadenie. Práve je pozastavené — dôvod aj ďalší krok sú nižšie.",
      column: "Verzia na UAT",
    },
    prod: {
      title: "PROD",
      intro:
        "Per-zákazník produkčné nasadenie. PROD je možné nasadiť až po akceptácii UAT danej verzie — bez akceptácie je Nasadiť zablokované.",
      introPaused: "Per-zákazník produkčné nasadenie. Práve je pozastavené — dôvod aj ďalší krok sú nižšie.",
      column: "Verzia v PROD",
    },
  };

// The one reason string for a role-blocked Akceptovať — used by the button tooltip and its visible note.
const ACCEPT_ROLE_REASON = "Akceptáciu (otvorenie PROD) môže vykonať iba Manažér.";
// The same treatment for the PROD "Nasadiť": the deploy route lets the project owner deploy to UAT but
// keeps PROD for the Manažér (ri), so on the PROD tab the button looked live to a Junior/Medior and then
// failed with 403 after the click. Disabled WITH a reason, exactly like Akceptovať.
const DEPLOY_PROD_ROLE_REASON = "Nasadenie do PROD môže vykonať iba Manažér.";

// v4.0.54: while a re-verification runs, the version leaves the finished state — so the matrix reads exactly
// like the blocked state for the WHOLE run (minutes). Poll quietly so the screen unblocks itself instead of
// leaving the manager to guess whether the click did anything.
const REVERIFY_POLL_INTERVAL_MS = 15000;


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
  // Audit Theme 4: the last DeployResult per customer (ok / url / bumped_to / warnings) — previously thrown
  // away, so the manager got no confirmation, no live link, no version-bump notice. It holds FAILED results
  // too (`ok: false` arrives as HTTP 200), and the render branches on `ok`.
  const [rowResult, setRowResult] = useState<Record<string, DeployResult>>({});
  // v4.0.30: the customer_id whose token-launch 'Spustiť' is mid-flight (minting + opening the app).
  const [launching, setLaunching] = useState<string | null>(null);

  /** ``quiet`` skips the loading state — used by the re-verify poll so the table doesn't flash. */
  const load = useCallback(
    (quiet = false) => {
      if (!slug) return;
      if (!quiet) setLoading(true);
      setLoadError(null);
      getDeployMatrix(slug)
        .then((next) => {
          setMatrix(next);
          // v4.0.54: drop picks that are no longer deployable. Without this a stale pick survived an
          // emptied list, so `pickedVersion` still returned a version and the DISABLED button carried the
          // POSITIVE tooltip "Nasadiť verziu X…" — the one explanation on screen asserting the opposite
          // of what the button did.
          setPicked((prev) => {
            const allowed = new Set(next.verified_versions);
            const kept = Object.fromEntries(Object.entries(prev).filter(([, v]) => allowed.has(v)));
            return Object.keys(kept).length === Object.keys(prev).length ? prev : kept;
          });
        })
        .catch((err) => {
          // A QUIET (background poll) failure must not blank the screen: the table already on screen is
          // still valid and the next tick retries. Only a load the user actually asked for reports an error,
          // because only that one leaves them looking at nothing.
          if (quiet) return;
          if (err instanceof ApiError) {
            setLoadError(humanizeApiError(err, "Načítanie zlyhalo").message);
          } else {
            setLoadError("Sieťová chyba pri načítavaní matice nasadení.");
          }
        })
        .finally(() => {
          if (!quiet) setLoading(false);
        });
    },
    [slug],
  );

  useEffect(() => {
    load();
  }, [load]);

  // Self-refresh while a re-verification is in flight, so "Nasadiť" re-opens on its own when it goes green.
  const reverifyRunning = matrix?.deployability?.cause === "reverify_running";
  useEffect(() => {
    if (!reverifyRunning) return;
    const timer = window.setInterval(() => load(true), REVERIFY_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [reverifyRunning, load]);

  function setRowMsg(customerId: string, msg: string | null) {
    setRowError((prev) => {
      const next = { ...prev };
      if (msg === null) delete next[customerId];
      else next[customerId] = msg;
      return next;
    });
  }

  /** v4.0.30: token-launch 'Spustiť' — mint a short-lived UAT test launch token server-side and open the
   * deployed app LOGGED-IN (no NEX Manager round-trip). UAT + token-launch apps only. */
  async function handleLaunch(customerId: string) {
    if (!slug) return;
    setLaunching(customerId);
    setRowMsg(customerId, null);
    try {
      const { launch_url } = await uatLaunch(customerId, slug);
      window.open(launch_url, "_blank", "noopener");
    } catch (err) {
      // This endpoint's 400/404 details are curated plain Slovak WITH a next step (e.g. "launch kľúč pre
      // UAT nie je nastavený" / "nie je token-launch aplikácia — použi Otvoriť aplikáciu") — show them
      // directly so a non-expert sees WHAT is wrong; anything else falls back to the generic humanised text.
      const curated =
        err instanceof ApiError &&
        (err.status === 400 || err.status === 404) &&
        typeof err.message === "string" &&
        err.message &&
        err.message !== "[object Object]"
          ? err.message
          : null;
      setRowMsg(
        customerId,
        curated ??
          (err instanceof ApiError ? humanizeApiError(err, "Spustenie zlyhalo").message : "Sieťová chyba pri spúšťaní."),
      );
    } finally {
      setLaunching(null);
    }
  }

  /** v4.0.30: the app-open control for a deployed cell. A token-launch app in UAT gets 'Spustiť'
   * (mint a short-lived test launch token → land logged-in); every other case keeps the plain
   * 'Otvoriť aplikáciu' link. Shared by the persistent cell link and the post-deploy result so an
   * operator never lands on a bare 'spusti z NEX Managera' page after a UAT deploy. */
  function renderLaunchOrOpen(customerId: string, url: string) {
    if (environment === "uat" && matrix?.auth_mode === "token") {
      return (
        <button
          type="button"
          onClick={() => handleLaunch(customerId)}
          disabled={launching === customerId}
          className="mt-1 flex items-center gap-1 text-[11px] text-primary-500 hover:underline disabled:opacity-60"
          title="Spustí appku prihlásenú (UAT test launch)"
        >
          {launching === customerId ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <ExternalLink className="h-3 w-3" />
          )}
          Spustiť
        </button>
      );
    }
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="mt-1 flex items-center gap-1 text-[11px] text-primary-500 hover:underline"
      >
        <ExternalLink className="h-3 w-3" /> Otvoriť aplikáciu
      </a>
    );
  }

  /** The version a row's Nasadiť will deploy: the explicit pick, else newest verified. */
  function pickedVersion(row: DeployMatrixRow): string | undefined {
    return picked[row.customer_id] ?? matrix?.verified_versions[0];
  }

  /**
   * Why this row's Nasadiť cannot run — the full sentence for the tooltip plus the short line rendered
   * under the button. Three blocks, checked in the order the backend would reject them:
   *   1. role — PROD deploy is ri-only (UAT deploy is open to the project owner), so this one is
   *      per-environment, not per-page;
   *   2. nothing deployable — no verified version at all (the cause is explained above the table);
   *   3. the PROD acceptance gate (§3.5) — that (version, customer) has no recorded UAT acceptance.
   * Returns null when the deploy is allowed.
   */
  function deployBlock(row: DeployMatrixRow): { title: string; note: string } | null {
    // The route's FIRST gate (owner-or-ri) applies to BOTH tabs, so it is checked before the
    // PROD-only one. Without it a Medior who does not own the project loaded the page — the matrix
    // read is laxer — and met a live-looking "Nasadiť" on UAT that 403s: the same defect one tab over.
    if (!canDeploy) {
      return {
        title: "Nasadzovať tento projekt môže jeho vlastník alebo Manažér.",
        note: "nemáš oprávnenie",
      };
    }
    if (environment === "prod" && !canDeployProd) {
      return { title: DEPLOY_PROD_ROLE_REASON, note: "nasadzuje Manažér" };
    }
    const version = pickedVersion(row);
    if (!version) return { title: "Žiadna overená verzia na nasadenie.", note: "pozastavené — dôvod hore" };
    if (environment === "prod" && !row.accepted_versions.includes(version)) {
      return {
        title: `PROD je zablokované: verzia ${fmtVer(version)} nemá akceptované UAT pre tohto zákazníka.`,
        note: "čaká na akceptáciu UAT",
      };
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
          // The backend gate (never bypassed) rejected. TWO shapes reach this, and calling both "akceptačná
          // brána" was factually wrong on UAT, where no acceptance gate exists (v4.0.54): (a) the real PROD
          // acceptance gate; (b) a verification that went stale between page load and click — the matrix is
          // a snapshot and the page does not poll while a deploy is still possible. Never echo the raw
          // English backend text; reload so the notice above states the real, current cause in Slovak.
          const acceptanceGate = environment === "prod" && !row.accepted_versions.includes(version);
          setRowMsg(
            row.customer_id,
            acceptanceGate
              ? "Nasadenie zablokované (akceptačná brána)."
              : "Nasadenie sa medzitým zablokovalo — dôvod je vysvetlený hore.",
          );
          load();
        } else if (err.status === 403) {
          setRowMsg(row.customer_id, "Nasadenie je dostupné len pre rolu Manažér.");
        } else {
          // v4.0.58: the deploy gates (missing admin password, missing launch wiring) answer with a curated
          // plain-Slovak sentence that NAMES what to fix. Passing those through `humanizeApiError` replaced
          // them with "zadané údaje nie sú v poriadku" and buried the real one in a technical detail this
          // page never renders — the same silence this batch keeps removing, one layer down. Show a curated
          // backend sentence as-is (mirrors the 'Spustiť' handler above); anything else stays humanised.
          const curated =
            (err.status === 400 || err.status === 422) &&
            typeof err.message === "string" &&
            err.message &&
            err.message !== "[object Object]"
              ? err.message
              : null;
          setRowMsg(row.customer_id, curated ?? humanizeApiError(err, "Nasadenie zlyhalo").message);
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
  // v4.0.55: acceptance is the ri-only PROD gate (D3) — an owner-Junior saw "Akceptovať" enabled on their
  // OWN project and got a 403 on click. Disabled-with-a-reason, never a live-looking dead button.
  const canAccept = matrix?.can_accept ?? false;
  // v4.0.55 fixed the same defect for "Akceptovať" only. PROD deploy carries its own ri-only gate on the
  // route, so a Junior owner / a Medior got a live-looking "Nasadiť" on the PROD tab and a 403 on click.
  const canDeployProd = matrix?.can_deploy_prod ?? false;
  // The other half of the same story: the deploy route refuses a non-owner Medior on BOTH tabs.
  const canDeploy = matrix?.can_deploy ?? false;
  // The intro tells the manager to deploy → test → accept. While deployment is paused that is an instruction
  // they cannot follow, sitting directly above a notice saying so — so the paused state gets its own line.
  const deployPaused = (matrix?.deployability?.cause ?? "ok") !== "ok";
  const currentCol = environment === "uat" ? (r: DeployMatrixRow) => r.uat_version : (r: DeployMatrixRow) => r.prod_version;

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-base font-bold text-[var(--color-text-primary)]">{labels.title}</h1>
        <button
          onClick={() => load()}
          title="Obnoviť"
          className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
        >
          <RefreshCw className="h-3.5 w-3.5" /> Obnoviť
        </button>
      </div>
      <p className="mb-4 text-xs text-[var(--color-text-muted)]">
        Projekt <span className="text-[var(--color-text-secondary)]">{selectedProject.name}</span>.{" "}
        {deployPaused ? labels.introPaused : labels.intro}
      </p>

      {/* v4.0.54: WHY Nasadiť is closed + the way out, on BOTH tabs. Renders nothing when a version is
          deployable. Placed ABOVE the table so it can't be read as a per-row detail — one commit in the
          project un-verifies every unreleased finished version at once, so the cause is project-wide. */}
      {matrix && slug && (
        <DeployBlockNotice
          block={matrix.deployability}
          projectSlug={slug}
          onReverifyStarted={() => load()}
        />
      )}

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
                const blocked = deployBlock(row);
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
                      {liveUrl && renderLaunchOrOpen(row.customer_id, liveUrl)}
                    </td>

                    {/* Nasadiť version picker (verified versions only) */}
                    <td className="px-3 py-3">
                      {verified.length === 0 ? (
                        <span className="text-xs text-[var(--color-text-muted)]">
                          nič na nasadenie — dôvod hore
                        </span>
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
                      {/* A RUNTIME deploy failure answers HTTP 200 with `ok: false` (build failed, container
                          never came up, '/api' silent, migration failed, public route down, launch ticket not
                          mintable) — so the catch above never runs and this block painted the green "✓ Nasadené"
                          over a deploy that did not happen. Branch on `result.ok`; the reason is the backend's own
                          non-secret `event.detail` (schemas/deploy.py). */}
                      {result && !isBusy && (
                        <div
                          className={`mt-1 space-y-0.5 text-[11px] ${
                            result.ok
                              ? "text-emerald-600 dark:text-emerald-400"
                              : "text-[var(--color-state-error-fg)]"
                          }`}
                        >
                          {result.ok ? (
                            <div>✓ Nasadené{result.bumped_to ? ` — projekt povýšený na ${fmtVer(result.bumped_to)}` : ""}</div>
                          ) : (
                            <div>
                              ✗ Nasadenie zlyhalo{result.event.detail ? ` — ${result.event.detail}` : ""}
                            </div>
                          )}
                          {result.ok && result.url && renderLaunchOrOpen(row.customer_id, result.url)}
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
                            disabled={isBusy || !row.uat_version || !canAccept}
                            title={
                              !canAccept
                                ? ACCEPT_ROLE_REASON
                                : row.uat_version
                                  ? `Akceptovať UAT verziu ${fmtVer(row.uat_version)}`
                                  : "Najprv nasaď verziu na UAT"
                            }
                            className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] disabled:opacity-40"
                          >
                            <CheckCircle2 className="h-3.5 w-3.5" /> Akceptovať
                          </button>
                        )}
                        {environment === "uat" && !canAccept && (
                          <span className="text-[11px] text-[var(--color-text-muted)]">
                            akceptáciu robí Manažér
                          </span>
                        )}
                        <button
                          onClick={() => handleDeploy(row)}
                          disabled={isBusy || verified.length === 0 || blocked !== null}
                          title={blocked?.title ?? `Nasadiť verziu ${fmtVer(chosen)} do ${labels.title}`}
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
                      {/* v4.0.54: this line used to be PROD-only and hardcoded to the acceptance gate — so on
                          UAT (the incident tab) a disabled button got no visible line at all, and on PROD it
                          asserted a cause that was often not the real one. Now both tabs get a line, and it
                          names which of the three blocks (role / nothing deployable / acceptance) is in force. */}
                      {blocked && (
                        <div className="mt-1 text-right text-[11px] text-[var(--color-text-muted)]">
                          {blocked.note}
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

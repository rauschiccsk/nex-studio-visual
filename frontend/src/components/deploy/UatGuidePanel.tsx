// DEV-90 — what to try in UAT, under the UAT table. DEV-91 — what was tried, and a change that does not work.
//
// 12.10.2026 the Director:
// „V záložke UAT by som privýtal súhrn toho čo mám otestovať v novej verzii.“
// The agent writes the version's test guide while it builds and the verification stage checks it; this panel
// shows the guide of the verified commit — or, for a version from before the guide, the changes its release note
// lists — with each customer's UAT link and the login of its first admin. Each change is ticked off as tried, or
// marked as not working with what happened; that prepares a fast fix the manager starts himself. What was tried is
// kept on the server per customer and version, and the acceptance records it.
//
// DEV-247 — 28.10.2026 NEX Manager for MÁGERSTAV: UAT got 1.4.1, a fast fix carrying 1.3.0 and 1.4.0 that MÁGERSTAV
// never accepted, and the guide showed 1.4.1 alone („Nie je čo skúšať“). For the version a customer runs on UAT the
// panel shows every version that UAT carries and the customer has not accepted (uat_versions_to_test), each under its
// number; what was tried is still kept per version.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, ExternalLink, ListChecks } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import { getUatChecks, getUatGuide, setUatCheck } from "@/services/api/deploy";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { fastFixText, handOffFastFix } from "@/lib/fastFixHandoff";
import { combineProgress } from "@/lib/uatTesting";
import type { DeployMatrixRow, UatGuide, UatProgress } from "@/types/deploy";

interface Props {
  slug: string;
  /** Verified versions, newest first — the ones UAT can get. */
  versions: string[];
  rows: DeployMatrixRow[];
}

interface Change {
  title: string;
  screen?: string | null;
  data?: string | null;
  steps: string[];
  expected: string[];
}

/** DEV-247: the versions to try — for the version the customer runs on UAT, every one it has not accepted. */
function versionsToTry(row: DeployMatrixRow | undefined, version: string): string[] {
  const pending = row?.uat_version === version ? (row?.uat_versions_to_test ?? []) : [];
  return pending.length > 0 ? pending : [version];
}

/** The note box belongs to one change of one version. */
function key(version: string, title: string): string {
  return `${version}\u0000${title}`;
}

/** The changes of a guide — its sections, or the release note's changes of a version from before the guide. */
function changesOf(guide: UatGuide | undefined): Change[] {
  return guide?.source === "guide"
    ? (guide.sections ?? []).map((s) => ({ ...s, steps: s.steps ?? [], expected: s.expected ?? [] }))
    : (guide?.changes ?? []).map((c) => ({ title: c, steps: [], expected: [] }));
}

/** The customer whose UAT is being tried: the one that runs this version there, else the first. */
function defaultCustomer(rows: DeployMatrixRow[], version: string): string {
  return (rows.find((r) => r.uat_version === version) ?? rows[0])?.customer_id ?? "";
}

export default function UatGuidePanel({ slug, versions, rows }: Props) {
  const navigate = useNavigate();
  const [version, setVersion] = useState<string>(versions[0] ?? "");
  const [customerId, setCustomerId] = useState<string>(() => defaultCustomer(rows, versions[0] ?? ""));
  const [guides, setGuides] = useState<Record<string, UatGuide>>({});
  const [progresses, setProgresses] = useState<Record<string, UatProgress>>({});
  const [error, setError] = useState<HumanError | null>(null);
  const [busy, setBusy] = useState(false);
  const [noting, setNoting] = useState<string | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!versions.includes(version)) setVersion(versions[0] ?? "");
  }, [versions, version]);

  useEffect(() => {
    if (!rows.some((r) => r.customer_id === customerId)) setCustomerId(defaultCustomer(rows, version));
  }, [rows, customerId, version]);

  const customer = rows.find((r) => r.customer_id === customerId);
  const toTry = version ? versionsToTry(customer, version) : [];
  const toTryKey = toTry.join(" ");

  useEffect(() => {
    if (!toTryKey) return;
    let live = true;
    setGuides({});
    setError(null);
    for (const v of toTryKey.split(" ")) {
      getUatGuide(slug, v)
        .then((g) => live && setGuides((all) => ({ ...all, [v]: g })))
        .catch((e) => live && setError(humanizeApiError(e, "Návod na skúšku sa nepodarilo načítať")));
    }
    return () => {
      live = false;
    };
  }, [slug, toTryKey]);

  useEffect(() => {
    if (!toTryKey || !customerId) return;
    let live = true;
    setProgresses({});
    for (const v of toTryKey.split(" ")) {
      getUatChecks(customerId, v)
        .then((p) => live && setProgresses((all) => ({ ...all, [v]: p })))
        .catch((e) => live && setError(humanizeApiError(e, "Čo už je vyskúšané, sa nepodarilo načítať")));
    }
    return () => {
      live = false;
    };
  }, [customerId, toTryKey]);

  if (!versions.length) return null;
  const guide = guides[version];
  const login = new Map((guide?.logins ?? []).map((l) => [l.customer_id, l.login]));
  const loaded = toTry.map((v) => progresses[v]).filter((p): p is UatProgress => p != null);
  const progress = loaded.length === toTry.length && loaded.length > 0 ? combineProgress(loaded) : null;
  const earlier = toTry.slice(0, -1);

  async function record(v: string, item: string, status: "tested" | "broken" | null, text?: string): Promise<boolean> {
    if (!customerId) return false;
    setBusy(true);
    setError(null);
    try {
      const saved = await setUatCheck(customerId, { version_number: v, item, status, note: text ?? null });
      setProgresses((all) => ({ ...all, [v]: saved }));
      return true;
    } catch (e) {
      setError(humanizeApiError(e, "Záznam zo skúšky sa nepodarilo uložiť"));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function prepareFix(v: string, change: Change, text: string) {
    if (!(await record(v, change.title, "broken", text))) return;
    try {
      const where = handOffFastFix(
        slug,
        fastFixText({
          version: v,
          customer: customer?.customer_name ?? "",
          title: change.title,
          steps: change.steps,
          expected: change.expected,
          note: text,
        }),
      );
      setNoting(null);
      setNote("");
      navigate(where);
    } catch (e) {
      setError(humanizeApiError(e, "Zadanie rýchlej opravy sa nepodarilo pripraviť"));
    }
  }

  /** DEV-247: one version's walkthrough — under its number when the customer has more than one to try. */
  function renderVersion(v: string) {
    const g = guides[v];
    const changes = changesOf(g);
    const checks = new Map((progresses[v]?.checks ?? []).map((c) => [c.item, c]));
    return (
      <div key={v}>
        {toTry.length > 1 && (
          <h3 className="mt-5 border-b border-[var(--color-border-default)] pb-1 text-sm font-semibold text-[var(--color-text-primary)]">
            Verzia {v}
          </h3>
        )}
          {!g && !error && <p className="mt-3 text-xs text-[var(--color-text-muted)]">Načítavam návod…</p>}

          {g?.source === "guide" && g.nothing_to_test && changes.length === 0 && (
            <p className="mt-3 text-sm text-[var(--color-text-secondary)]">Nie je čo skúšať: {g.nothing_to_test}</p>
          )}
          {g?.source === "release_notes" && (
            <p className="mt-3 text-xs text-[var(--color-text-muted)]">
              Návod na skúšku pre túto verziu nevznikol — toto sú zmeny z jej poznámok k vydaniu:
            </p>
          )}
          {changes.length > 0 && (
            <ol className="mt-3 space-y-3">
              {changes.map((change) => {
                const check = checks.get(change.title);
                const broken = check?.status === "broken";
                return (
                  <li key={change.title} className="rounded border border-[var(--color-border-default)] p-3">
                    <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{change.title}</h3>
                    {(change.screen || change.data) && (
                      <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                        {change.screen && <>Obrazovka: {change.screen}</>}
                        {change.screen && change.data && " · "}
                        {change.data && <>Testovacie údaje: {change.data}</>}
                      </p>
                    )}
                    {change.steps.length > 0 && (
                      <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-sm text-[var(--color-text-primary)]">
                        {change.steps.map((step, j) => (
                          <li key={j}>{step}</li>
                        ))}
                      </ol>
                    )}
                    {change.expected.map((e, j) => (
                      <p key={j} className="mt-2 text-sm text-[var(--color-state-success-fg)]">
                        Očakávané: {e}
                      </p>
                    ))}

                    {customerId && progresses[v] && (
                      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
                        {broken ? (
                          <>
                            <span className="inline-flex items-center gap-1 text-[var(--color-state-warning-fg)]">
                              <AlertTriangle className="h-3.5 w-3.5" aria-hidden /> Nefunguje
                              {check?.note ? `: ${check.note}` : ""}
                            </span>
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => void prepareFix(v, change, check?.note ?? "")}
                              className="rounded border border-[var(--color-border-default)] px-2 py-1 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] disabled:opacity-50"
                            >
                              Pripraviť rýchlu opravu
                            </button>
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => void record(v, change.title, null)}
                              className="rounded px-2 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] disabled:opacity-50"
                            >
                              Zrušiť záznam
                            </button>
                          </>
                        ) : (
                          <>
                            <label className="inline-flex cursor-pointer items-center gap-1.5 text-[var(--color-text-secondary)] pointer-coarse:min-h-11">
                              <input
                                type="checkbox"
                                checked={check?.status === "tested"}
                                disabled={busy}
                                onChange={(e) => void record(v, change.title, e.target.checked ? "tested" : null)}
                              />
                              Vyskúšané
                            </label>
                            <button
                              type="button"
                              aria-expanded={noting === key(v, change.title)}
                              disabled={busy}
                              onClick={() => {
                                setNoting(noting === key(v, change.title) ? null : key(v, change.title));
                                setNote("");
                              }}
                              className="rounded border border-[var(--color-border-default)] px-2 py-1 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] disabled:opacity-50"
                            >
                              Nefunguje
                            </button>
                          </>
                        )}
                      </div>
                    )}
                    {noting === key(v, change.title) && !broken && (
                      <div className="mt-2 space-y-2">
                        <label className="block text-xs text-[var(--color-text-secondary)]">
                          Čo sa stalo?
                          <textarea
                            data-no-draft="krátka poznámka ku skúške — hneď sa z nej stane zadanie rýchlej opravy, ktoré sa už pamätá"
                            lang="sk"
                            spellCheck={true}
                            rows={3}
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1 text-sm text-[var(--color-text-primary)]"
                          />
                        </label>
                        <button
                          type="button"
                          disabled={busy || !note.trim()}
                          onClick={() => void prepareFix(v, change, note)}
                          className="rounded bg-primary-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-50"
                        >
                          Pripraviť rýchlu opravu
                        </button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
          {g?.source === "none" && (
            <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
              Pre túto verziu nie je čo ukázať — nemá návod na skúšku ani poznámky k vydaniu.
            </p>
          )}
      </div>
    );
  }

  return (
    <section aria-labelledby="uat-guide-heading" className="mt-6 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] p-4">
      <div className="flex flex-wrap items-center gap-2">
        <ListChecks className="h-4 w-4 text-[var(--color-text-secondary)]" aria-hidden />
        <h2 id="uat-guide-heading" className="text-sm font-semibold text-[var(--color-text-primary)]">
          Čo otestovať vo verzii
        </h2>
        <select
          aria-label="Verzia, ktorú skúšaš"
          value={version}
          onChange={(e) => setVersion(e.target.value)}
          className="max-w-full min-w-0 rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1 text-sm text-[var(--color-text-primary)]"
        >
          {versions.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
        {rows.length > 1 && (
          <select
            aria-label="Zákazník, u ktorého skúšaš"
            value={customerId}
            onChange={(e) => setCustomerId(e.target.value)}
            className="max-w-full min-w-0 rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1 text-sm text-[var(--color-text-primary)]"
          >
            {rows.map((r) => (
              <option key={r.customer_id} value={r.customer_id}>
                {r.customer_name}
              </option>
            ))}
          </select>
        )}
        {progress && progress.total > 0 && (
          <span className="text-xs text-[var(--color-text-secondary)]">
            Otestované {progress.tested} z {progress.total}
            {(progress.broken ?? []).length > 0 && ` · nefunguje ${(progress.broken ?? []).length}`}
          </span>
        )}
      </div>

      {rows.some((r) => r.uat_url) && (
        <ul className="mt-3 space-y-1 text-xs text-[var(--color-text-secondary)]">
          {rows
            .filter((r) => r.uat_url)
            .map((r) => (
              <li key={r.customer_id} className="flex flex-wrap items-center gap-x-2">
                <span className="font-medium text-[var(--color-text-primary)]">{r.customer_name}:</span>
                {(r.uat_urls ?? []).length === 0 ? (
                  <a
                    href={r.uat_url ?? undefined}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-primary-400 hover:text-primary-300 pointer-coarse:min-h-11 pointer-coarse:min-w-11"
                  >
                    <ExternalLink className="h-3 w-3" aria-hidden /> UAT
                  </a>
                ) : (
                  // DEV-164: a Web project — the administration and the web each have their address.
                  (r.uat_urls ?? []).map((link) => (
                    <a
                      key={link.url}
                      href={link.url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-primary-400 hover:text-primary-300 pointer-coarse:min-h-11 pointer-coarse:min-w-11"
                    >
                      <ExternalLink className="h-3 w-3" aria-hidden /> {link.label}
                    </a>
                  ))
                )}
                {login.get(r.customer_id) && <span>prihlásenie: {login.get(r.customer_id)}</span>}
              </li>
            ))}
        </ul>
      )}

      <ErrorNote error={error} className="mt-3" />
      {earlier.length > 0 && customer && (
        <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
          {`Na UAT je verzia ${version} a nesie aj verzie ${earlier.join(", ")}, ktoré ${customer.customer_name} ešte neakceptoval — vyskúšaj všetky.`}
        </p>
      )}
      {toTry.map((v) => renderVersion(v))}
    </section>
  );
}

import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import { listVersions, createVersion } from "@/services/api/versions";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function nextVersionNumber(versions: Version[]): string {
  if (versions.length === 0) return "v0.1";
  const last = [...versions].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )[0];
  if (!last) return "v0.1";
  const m = last.version_number.match(/^v?(\d+)\.(\d+)$/);
  if (!m || !m[1] || !m[2]) return "";
  const maj = m[1];
  const min = m[2];
  const nextMin = parseInt(min) + 1;
  return nextMin >= 10 ? `v${parseInt(maj) + 1}.0` : `v${maj}.${nextMin}`;
}

// ─── Input style ──────────────────────────────────────────────────────────────

const inputCls =
  "w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary-500 transition-colors";

// ─── NewVersionPage ───────────────────────────────────────────────────────────

export default function NewVersionPage() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [prevVersions, setPrevVersions] = useState<Version[]>([]);
  const [loadError, setLoadError] = useState("");

  const [versionNumber, setVersionNumber] = useState("");
  const [versionManual, setVersionManual] = useState(false);
  const [name, setName] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [description, setDescription] = useState("");

  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");
  const [loading, setLoading] = useState(false);

  const verRef = useRef<HTMLInputElement>(null);

  // Load project + existing versions
  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    listProjectsApi({ limit: 100 })
      .then((res) => {
        if (cancelled) return;
        const found = res.items.find((p) => p.slug === slug);
        if (!found) { setLoadError("Projekt nebol nájdený."); return; }
        setProject(found);
        return listVersions(found.id).then((vs) => {
          if (cancelled) return;
          setPrevVersions(vs);
          if (!versionManual) setVersionNumber(nextVersionNumber(vs));
        });
      })
      .catch(() => { if (!cancelled) setLoadError("Nepodarilo sa načítať projekt."); });
    return () => { cancelled = true; };
  }, [slug]);

  useEffect(() => { verRef.current?.focus(); }, []);

  const lastVersion = prevVersions.length > 0
    ? [...prevVersions].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0]
    : null;

  function validate(): boolean {
    const next: Record<string, string> = {};
    if (!versionNumber.trim()) next.versionNumber = "Číslo verzie je povinné.";
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!project || !validate()) return;
    setFormError("");
    setLoading(true);
    try {
      const v = await createVersion(project.id, {
        version_number: versionNumber.trim(),
        name: name.trim() || undefined,
        description: description.trim() || undefined,
        target_date: targetDate || undefined,
      });
      navigate(`/projects/${slug}/versions/${v.id}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Nepodarilo sa vytvoriť verziu.";
      setFormError(msg);
    } finally {
      setLoading(false);
    }
  }

  if (loadError) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <div className="rounded-lg bg-red-500/10 border border-red-500/30 p-4 text-sm text-red-400">
          {loadError}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex-shrink-0 px-6 py-4 border-b border-slate-800 flex items-center gap-3 bg-slate-950">
        <button
          onClick={() => navigate(`/projects/${slug}`)}
          className="text-slate-500 hover:text-slate-300 transition-colors"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <div>
          <h1 className="text-base font-bold text-slate-100">Nová verzia</h1>
          {project && (
            <p className="text-xs text-slate-500 mt-0.5">
              {project.name}
              {lastVersion && (
                <>
                  {" · follows "}
                  <span className="text-slate-400 font-mono">{lastVersion.version_number}</span>
                </>
              )}
            </p>
          )}
        </div>
      </div>

      {/* Scrollable form */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-xl mx-auto">
          <form onSubmit={handleSubmit} noValidate className="space-y-5">

            {/* Version number + Name */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <label className="block text-sm font-medium text-slate-300">
                    Číslo verzie *
                  </label>
                  {!versionManual && versionNumber && (
                    <span className="text-[10px] text-primary-400/70 font-normal">automaticky navrhnuté</span>
                  )}
                </div>
                <input
                  ref={verRef}
                  type="text"
                  placeholder="v0.1"
                  autoComplete="off"
                  spellCheck={false}
                  value={versionNumber}
                  onChange={(e) => {
                    setVersionNumber(e.target.value);
                    setVersionManual(true);
                    if (errors.versionNumber) setErrors((er) => ({ ...er, versionNumber: "" }));
                  }}
                  className={`${inputCls} font-mono ${errors.versionNumber ? "border-red-500/50" : ""}`}
                />
                {errors.versionNumber && (
                  <p className="mt-1 text-xs text-red-400">{errors.versionNumber}</p>
                )}
                <p className="mt-1 text-[11px] text-slate-600">Začnite na v0.1 · v1.0 = prvé produkčné vydanie</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">
                  Názov <span className="text-slate-600 font-normal text-xs">(voliteľné)</span>
                </label>
                <input
                  type="text"
                  placeholder="napr. platobný modul"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className={inputCls}
                />
              </div>
            </div>

            {/* Target date */}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">
                Cieľový dátum <span className="text-slate-600 font-normal text-xs">(voliteľné)</span>
              </label>
              <input
                type="date"
                value={targetDate}
                onChange={(e) => setTargetDate(e.target.value)}
                className={`${inputCls} [color-scheme:dark]`}
              />
            </div>

            {/* Previous version context */}
            {lastVersion && (
              <div className="rounded-lg border border-slate-700 bg-slate-800/40 p-4">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-xs text-slate-500">Predchádzajúca verzia</span>
                  <span className="text-xs font-medium text-slate-300 bg-slate-700 px-2.5 py-1 rounded font-mono">
                    {lastVersion.version_number}
                  </span>
                </div>
                <div className="flex items-start gap-3 pt-3 border-t border-slate-700/60">
                  <input
                    type="checkbox"
                    id="inherit-design"
                    defaultChecked
                    className="mt-0.5 accent-indigo-500 shrink-0 cursor-pointer"
                  />
                  <label htmlFor="inherit-design" className="cursor-pointer">
                    <div className="text-xs text-slate-300 font-medium">
                      Zdediť DESIGN.md z {lastVersion.version_number}
                    </div>
                    <div className="text-[11px] text-slate-600 mt-0.5">
                      AI použije predchádzajúcu architektúru ako východiskový bod
                    </div>
                  </label>
                </div>
              </div>
            )}

            {/* Description / Intent */}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">
                Zámer verzie{" "}
                <span className="ml-1 text-slate-600 font-normal text-xs">(3–5 viet, nie celá špecifikácia)</span>
              </label>
              <textarea
                rows={4}
                placeholder="Príklad: Pridať platobný modul cez Tatra banku. Zákazník potrebuje automatické párovanie platieb s faktúrami a emailové notifikácie. Cieľ: funkčné platby pre pilotného zákazníka do konca mája."
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className={`${inputCls} resize-none leading-relaxed`}
              />
              <p className="text-[11px] text-slate-600 mt-1.5">
                Raw Spec sa zadáva v kroku 1 pipeline po vytvorení verzie.
              </p>
            </div>

            {/* Error banner */}
            {formError && (
              <div className="rounded-lg bg-red-500/10 border border-red-500/30 p-3 text-sm text-red-400">
                {formError}
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-3 pt-1">
              <button
                type="button"
                onClick={() => navigate(`/projects/${slug}`)}
                className="flex-1 px-4 py-2 text-sm text-slate-400 border border-slate-700 rounded-lg hover:bg-slate-800 transition-colors"
              >
                Zrušiť
              </button>
              <button
                type="submit"
                disabled={loading || !project}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-white bg-primary-600 hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-colors"
              >
                {loading ? (
                  <>
                    <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                    Vytváram…
                  </>
                ) : (
                  <>
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5 5m5-5H6" />
                    </svg>
                    Vytvoriť verziu
                  </>
                )}
              </button>
            </div>

          </form>
        </div>
      </div>
    </div>
  );
}

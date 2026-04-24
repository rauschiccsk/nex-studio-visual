import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import { getVersion } from "@/services/api/versions";
import { listProfessionalSpecs } from "@/services/api/professionalSpecifications";
import { listUIDesigns } from "@/services/api/uiDesigns";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";
import type { ProfessionalSpecificationRead } from "@/types/professionalSpecification";
import type { UIDesignRead } from "@/types/uiDesign";

// ─── SummaryPage — Step 3 ─────────────────────────────────────────────────────

export default function SummaryPage() {
  const { slug, versionId } = useParams<{ slug: string; versionId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [version, setVersion] = useState<Version | null>(null);
  const [profSpec, setProfSpec] = useState<ProfessionalSpecificationRead | null>(null);
  const [uiDesign, setUIDesign] = useState<UIDesignRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!slug || !versionId) return;
    let cancelled = false;
    Promise.all([
      listProjectsApi({ limit: 100 }).then((res) => res.items.find((p) => p.slug === slug) ?? null),
      getVersion(versionId),
    ]).then(([proj, ver]) => {
      if (cancelled || !proj) { setError("Projekt nebol nájdený."); setLoading(false); return; }
      setProject(proj);
      setVersion(ver);
      return Promise.all([
        listProfessionalSpecs({ project_id: proj.id, limit: 1 }),
        listUIDesigns({ project_id: proj.id, limit: 1 }),
      ]).then(([profRes, uiRes]) => {
        if (cancelled) return;
        setProfSpec(profRes.items[0] ?? null);
        setUIDesign(uiRes.items[0] ?? null);
        setLoading(false);
      });
    }).catch(() => { if (!cancelled) { setError("Nepodarilo sa načítať dáta."); setLoading(false); } });
    return () => { cancelled = true; };
  }, [slug, versionId]);

  if (loading) return <LoadingSpinner />;
  if (error || !project || !version) return <ErrorPanel msg={error} />;

  const profSpecApproved = !!profSpec?.approved_at;
  const uiDesignApproved = !!uiDesign?.approved_at;
  const isApproved = profSpecApproved && uiDesignApproved;

  return (
    <div className="flex flex-col h-full">
      <StepHeader project={project} version={version} slug={slug!} versionId={versionId!} stepN={3} stepLabel="Súhrnná dokumentácia" />

      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-3xl mx-auto">

          {/* Gate: parallel phase not fully approved */}
          {!isApproved && (
            <div className="rounded-xl border border-dashed border-slate-700 p-8 text-center">
              <svg className="w-10 h-10 text-slate-700 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
              <p className="text-sm text-slate-400 mb-1">Čaká na dokončenie Kroku 2</p>
              <p className="text-xs text-slate-600 mb-5">Oba sub-kroky musia byť schválené pred pokračovaním.</p>

              {/* 2A + 2B status */}
              <div className="grid grid-cols-2 gap-3 max-w-sm mx-auto mb-5">
                <div className={`rounded-lg border p-3 text-left ${profSpecApproved ? "border-green-500/25 bg-green-500/5" : "border-slate-700 bg-slate-800/50"}`}>
                  <div className="flex items-center gap-1.5 mb-1">
                    {profSpecApproved
                      ? <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
                      : profSpec
                      ? <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" />
                      : <span className="w-1.5 h-1.5 rounded-full bg-slate-600" />}
                    <span className={`text-[10px] font-bold uppercase tracking-wider ${profSpecApproved ? "text-green-400" : "text-slate-500"}`}>2A</span>
                  </div>
                  <p className="text-xs text-slate-400">Vývojová dokumentácia</p>
                  <p className="text-[10px] text-slate-600 mt-0.5">
                    {profSpecApproved ? "Schválená" : profSpec ? "Čaká na schválenie" : "Nevygenerovaná"}
                  </p>
                </div>
                <div className={`rounded-lg border p-3 text-left ${uiDesignApproved ? "border-green-500/25 bg-green-500/5" : "border-slate-700 bg-slate-800/50"}`}>
                  <div className="flex items-center gap-1.5 mb-1">
                    {uiDesignApproved
                      ? <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
                      : uiDesign
                      ? <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" />
                      : <span className="w-1.5 h-1.5 rounded-full bg-slate-600" />}
                    <span className={`text-[10px] font-bold uppercase tracking-wider ${uiDesignApproved ? "text-green-400" : "text-slate-500"}`}>2B</span>
                  </div>
                  <p className="text-xs text-slate-400">UI Design</p>
                  <p className="text-[10px] text-slate-600 mt-0.5">
                    {uiDesignApproved ? "Schválený" : uiDesign ? "Čaká na schválenie" : "Nevytvorený"}
                  </p>
                </div>
              </div>

              <div className="flex items-center justify-center gap-2">
                {!profSpecApproved && (
                  <button
                    onClick={() => navigate(`/projects/${slug}/versions/${versionId}/profspec`)}
                    className="text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 px-3 py-1.5 rounded-lg font-medium transition-colors"
                  >
                    ← 2A Vývojová dok.
                  </button>
                )}
                {!uiDesignApproved && (
                  <button
                    onClick={() => navigate(`/projects/${slug}/versions/${versionId}/uidesign`)}
                    className="text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 px-3 py-1.5 rounded-lg font-medium transition-colors"
                  >
                    ← 2B UI Design
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Approved: show summary placeholder */}
          {isApproved && profSpec && (
            <div className="space-y-4">
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-500/10 border border-green-500/25 text-green-400">
                  ✓ 2A Vývojová dokumentácia
                </span>
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-500/10 border border-green-500/25 text-green-400">
                  ✓ 2B UI Design
                </span>
                <div className="flex-1" />
                <button
                  onClick={() => navigate(`/projects/${slug}/versions/${versionId}/architecture`)}
                  className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
                >
                  Krok 4 →
                </button>
              </div>

              {/* Summary content — derived from professional spec */}
              <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
                <div className="flex items-center gap-2 mb-4">
                  <h2 className="text-sm font-semibold text-slate-200">Súhrnná dokumentácia zmien</h2>
                  <span className="text-[10px] text-slate-600">· {project.name} {version.version_number}</span>
                </div>
                <div className="text-xs text-slate-500 mb-3">
                  Súhrnná dokumentácia je automaticky odvodená z vývojovej dokumentácie.
                  Plná implementácia so samostatným generovaním bude dostupná v ďalšej verzii.
                </div>
                <div className="rounded-lg border border-slate-700 bg-slate-950 p-4">
                  <pre className="text-xs text-slate-400 font-mono whitespace-pre-wrap leading-relaxed line-clamp-20 overflow-hidden">
                    {profSpec.content}
                  </pre>
                </div>
                <div className="mt-3 text-[10px] text-slate-600 text-right">
                  Schválená: {new Date(profSpec.approved_at!).toLocaleString("sk-SK")}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Shared ───────────────────────────────────────────────────────────────────

function StepHeader({ project, version, slug, versionId, stepN, stepLabel }: {
  project: ProjectRead; version: Version; slug: string; versionId: string; stepN: number; stepLabel: string;
}) {
  const navigate = useNavigate();
  return (
    <div className="flex-shrink-0 bg-slate-900/50 border-b border-slate-800 px-5 py-2.5 flex items-center gap-3">
      <button onClick={() => navigate(`/projects/${slug}/versions/${versionId}`)} className="text-slate-500 hover:text-slate-300 transition-colors">
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
      </button>
      <span className="text-xs text-slate-400">{project.name}</span>
      <span className="text-slate-600">·</span>
      <span className="text-xs font-mono bg-slate-800 text-slate-300 px-2 py-0.5 rounded">{version.version_number}</span>
      <span className="text-slate-600">·</span>
      <span className="text-xs font-medium text-primary-400">Krok {stepN}/7 — {stepLabel}</span>
    </div>
  );
}

function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center py-20 text-slate-500 text-sm gap-2">
      <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      Načítavam…
    </div>
  );
}

function ErrorPanel({ msg }: { msg: string }) {
  return (
    <div className="p-6 max-w-3xl mx-auto">
      <div className="rounded-lg bg-red-500/10 border border-red-500/30 p-4 text-sm text-red-400">
        {msg || "Nepodarilo sa načítať dáta."}
      </div>
    </div>
  );
}

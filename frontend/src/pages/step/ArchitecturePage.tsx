import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import { getVersion } from "@/services/api/versions";
import { listProfessionalSpecs, generateDesignDoc } from "@/services/api/professionalSpecifications";
import { listDesignDocuments } from "@/services/api/designDocuments";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";
import type { ProfessionalSpecificationRead } from "@/types/professionalSpecification";
import type { DesignDocumentRead, DesignDocumentType } from "@/types/designDocument";

// ─── ArchitecturePage — Step 4 ───────────────────────────────────────────────

type DocTab = "behavior" | "design";

export default function ArchitecturePage() {
  const { slug, versionId } = useParams<{ slug: string; versionId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [version, setVersion] = useState<Version | null>(null);
  const [profSpec, setProfSpec] = useState<ProfessionalSpecificationRead | null>(null);
  const [behaviorDoc, setBehaviorDoc] = useState<DesignDocumentRead | null>(null);
  const [designDoc, setDesignDoc] = useState<DesignDocumentRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [activeTab, setActiveTab] = useState<DocTab>("behavior");

  // Generation
  const [generating, setGenerating] = useState<DesignDocumentType | null>(null);
  const [genOutput, setGenOutput] = useState("");
  const [genError, setGenError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const outputRef = useRef<HTMLPreElement>(null);

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
        listDesignDocuments({ project_id: proj.id, doc_type: "behavior", limit: 1 }),
        listDesignDocuments({ project_id: proj.id, doc_type: "design", limit: 1 }),
      ]).then(([profRes, behRes, desRes]) => {
        if (cancelled) return;
        setProfSpec(profRes.items[0] ?? null);
        setBehaviorDoc(behRes.items[0] ?? null);
        setDesignDoc(desRes.items[0] ?? null);
        setLoading(false);
      });
    }).catch(() => { if (!cancelled) { setError("Nepodarilo sa načítať dáta."); setLoading(false); } });
    return () => { cancelled = true; };
  }, [slug, versionId]);

  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight;
  }, [genOutput]);

  function handleGenerate(docType: DesignDocumentType) {
    if (!profSpec || generating) return;
    setGenerating(docType);
    setGenOutput("");
    setGenError("");
    abortRef.current = generateDesignDoc(
      profSpec.id,
      docType,
      (chunk) => setGenOutput((prev) => prev + chunk),
      () => {
        setGenerating(null);
        // Reload docs
        if (project) {
          listDesignDocuments({ project_id: project.id, doc_type: docType, limit: 1 }).then((res) => {
            const doc = res.items[0] ?? null;
            if (docType === "behavior") setBehaviorDoc(doc);
            else setDesignDoc(doc);
          });
        }
      },
      (err) => { setGenerating(null); setGenError(err.message); },
      (reason) => { setGenerating(null); setGenError(`Validation error: ${reason}`); },
    );
  }

  if (loading) return <LoadingSpinner />;
  if (error || !project || !version) return <ErrorPanel msg={error} />;

  const hasApprovedProfSpec = !!profSpec?.approved_at;
  const hasBehavior = !!behaviorDoc;
  const hasDesign = !!designDoc;
  const hasBoth = hasBehavior && hasDesign;

  const activeDoc = activeTab === "behavior" ? behaviorDoc : designDoc;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
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
        <span className="text-xs font-medium text-primary-400">Krok 4/7 — Architecture</span>
        <div className="flex-1" />
        {hasBoth && (
          <button
            onClick={() => navigate(`/projects/${slug}/versions/${versionId}/audit`)}
            className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
          >
            Krok 5 →
          </button>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">

        {/* Gate: no approved prof spec */}
        {!hasApprovedProfSpec && (
          <div className="flex flex-col items-center justify-center h-full p-10 text-center">
            <svg className="w-12 h-12 text-slate-700 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
            <p className="text-sm text-slate-500 mb-1">Čaká na schválenie Kroku 2</p>
            <p className="text-xs text-slate-700 mb-4">Architecture dokumenty sa vygenerujú po schválení profesionálnej špecifikácie.</p>
            <button
              onClick={() => navigate(`/projects/${slug}/versions/${versionId}/profspec`)}
              className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
            >
              ← Krok 2 — Vývojová dokumentácia
            </button>
          </div>
        )}

        {/* Generate panel */}
        {hasApprovedProfSpec && !hasBoth && !generating && (
          <div className="p-6 max-w-3xl mx-auto space-y-4">
            <p className="text-xs text-slate-600">
              Generuj BEHAVIOR.md a DESIGN.md z profesionálnej špecifikácie. Oba dokumenty musíš vygenerovať.
            </p>
            <div className="grid grid-cols-2 gap-4">
              {/* BEHAVIOR.md */}
              <div className={`rounded-xl border p-5 ${hasBehavior ? "border-green-500/25 bg-green-500/5" : "border-slate-700 bg-slate-900"}`}>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs font-mono font-bold text-slate-300">BEHAVIOR.md</span>
                  {hasBehavior && <span className="text-[10px] text-green-400">✓ vygenerovaný</span>}
                </div>
                <p className="text-xs text-slate-600 mb-3">Business rules, workflow, edge cases, UI behavior.</p>
                <button
                  onClick={() => handleGenerate("behavior")}
                  disabled={!!generating}
                  className={`w-full text-xs px-3 py-1.5 rounded-lg font-medium transition-colors ${
                    hasBehavior
                      ? "text-slate-500 border border-slate-700 hover:border-slate-600"
                      : "bg-primary-600 hover:bg-primary-500 text-white"
                  }`}
                >
                  {hasBehavior ? "Regenerovať" : "Generovať BEHAVIOR.md"}
                </button>
              </div>

              {/* DESIGN.md */}
              <div className={`rounded-xl border p-5 ${hasDesign ? "border-green-500/25 bg-green-500/5" : "border-slate-700 bg-slate-900"}`}>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs font-mono font-bold text-slate-300">DESIGN.md</span>
                  {hasDesign && <span className="text-[10px] text-green-400">✓ vygenerovaný</span>}
                </div>
                <p className="text-xs text-slate-600 mb-3">Architektúra, DB schéma, API endpointy, frontend komponenty.</p>
                <button
                  onClick={() => handleGenerate("design")}
                  disabled={!!generating}
                  className={`w-full text-xs px-3 py-1.5 rounded-lg font-medium transition-colors ${
                    hasDesign
                      ? "text-slate-500 border border-slate-700 hover:border-slate-600"
                      : "bg-primary-600 hover:bg-primary-500 text-white"
                  }`}
                >
                  {hasDesign ? "Regenerovať" : "Generovať DESIGN.md"}
                </button>
              </div>
            </div>

            {genError && (
              <div className="rounded-lg bg-red-500/10 border border-red-500/20 p-3 text-xs text-red-400">{genError}</div>
            )}
          </div>
        )}

        {/* Generating */}
        {generating && (
          <div className="p-6 max-w-3xl mx-auto space-y-3">
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <svg className="w-4 h-4 animate-spin text-primary-400" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              Generujem {generating === "behavior" ? "BEHAVIOR.md" : "DESIGN.md"}…
              <button onClick={() => abortRef.current?.abort()} className="ml-auto text-xs text-slate-600 hover:text-red-400 transition-colors">Zastaviť</button>
            </div>
            <pre
              ref={outputRef}
              className="rounded-xl border border-slate-800 bg-slate-950 p-4 text-xs text-slate-300 font-mono whitespace-pre-wrap leading-relaxed overflow-y-auto max-h-[60vh]"
            >{genOutput}<span className="inline-block w-0.5 h-3.5 bg-primary-400 animate-pulse ml-0.5 align-bottom" /></pre>
          </div>
        )}

        {/* Both docs available — tabs view */}
        {hasBoth && !generating && (
          <div className="flex flex-col h-full">
            {/* Tab bar */}
            <div className="flex-shrink-0 border-b border-slate-800 px-5 flex items-center gap-0">
              {(["behavior", "design"] as DocTab[]).map((t) => (
                <button
                  key={t}
                  onClick={() => setActiveTab(t)}
                  className={`px-4 py-2.5 text-xs font-medium font-mono border-b-2 transition-colors ${
                    activeTab === t
                      ? "border-primary-500 text-primary-400"
                      : "border-transparent text-slate-500 hover:text-slate-300"
                  }`}
                >
                  {t === "behavior" ? "BEHAVIOR.md" : "DESIGN.md"}
                </button>
              ))}
              <div className="flex-1" />
              <button
                onClick={() => handleGenerate(activeTab)}
                disabled={!!generating}
                className="text-[10px] text-slate-500 hover:text-slate-300 border border-slate-700 px-2 py-1 rounded transition-colors mr-2"
              >
                Regenerovať
              </button>
              {activeDoc?.approved_at ? (
                <span className="text-[10px] text-green-400 font-medium mr-2">✓ Schválený</span>
              ) : null}
            </div>
            {/* Content */}
            <div className="flex-1 overflow-y-auto p-5">
              {activeDoc ? (
                <pre className="text-sm text-slate-300 font-mono whitespace-pre-wrap leading-relaxed max-w-4xl mx-auto">
                  {activeDoc.content}
                </pre>
              ) : (
                <div className="text-center text-xs text-slate-700 py-10">Žiadny dokument</div>
              )}
            </div>
          </div>
        )}
      </div>
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

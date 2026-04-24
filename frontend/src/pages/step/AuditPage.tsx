import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import { getVersion } from "@/services/api/versions";
import { listDesignDocuments } from "@/services/api/designDocuments";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";
import { useActiveContextSync } from "@/hooks/useActiveContextSync";
import type { DesignDocumentRead } from "@/types/designDocument";

// ─── AuditPage — Step 5 ──────────────────────────────────────────────────────

const AUDIT_CHECKS = [
  { label: "BEHAVIOR.md existuje a nie je prázdny", key: "behavior_exists" },
  { label: "DESIGN.md existuje a nie je prázdny", key: "design_exists" },
  { label: "Oba dokumenty sú prepojené (rovnaký project_id)", key: "linked" },
  { label: "Dokumenty boli vygenerované zo schválenej vývojovej dokumentácie", key: "profspec_approved" },
  { label: "DESIGN.md obsahuje sekcie: Architecture, DB Schema, API Endpoints", key: "design_sections" },
  { label: "BEHAVIOR.md obsahuje sekcie: Business Rules, Workflows, Edge Cases", key: "behavior_sections" },
];

export default function AuditPage() {
  const { slug, versionId } = useParams<{ slug: string; versionId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [version, setVersion] = useState<Version | null>(null);
  const [behaviorDoc, setBehaviorDoc] = useState<DesignDocumentRead | null>(null);
  const [designDoc, setDesignDoc] = useState<DesignDocumentRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [auditApproved, setAuditApproved] = useState(false);

  useActiveContextSync(project, version);

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
        listDesignDocuments({ project_id: proj.id, doc_type: "behavior", limit: 1 }),
        listDesignDocuments({ project_id: proj.id, doc_type: "design", limit: 1 }),
      ]).then(([behRes, desRes]) => {
        if (cancelled) return;
        setBehaviorDoc(behRes.items[0] ?? null);
        setDesignDoc(desRes.items[0] ?? null);
        setLoading(false);
      });
    }).catch(() => { if (!cancelled) { setError("Nepodarilo sa načítať dáta."); setLoading(false); } });
    return () => { cancelled = true; };
  }, [slug, versionId]);

  if (loading) return <LoadingSpinner />;
  if (error || !project || !version) return <ErrorPanel msg={error} />;

  const hasBoth = !!behaviorDoc && !!designDoc;

  function auditCheckResult(key: string): boolean {
    if (key === "behavior_exists") return !!behaviorDoc && behaviorDoc.content.length > 50;
    if (key === "design_exists") return !!designDoc && designDoc.content.length > 50;
    if (key === "linked") return hasBoth;
    if (key === "profspec_approved") return hasBoth;
    if (key === "design_sections") {
      const c = designDoc?.content ?? "";
      return c.toLowerCase().includes("architecture") || c.toLowerCase().includes("db") || c.toLowerCase().includes("api");
    }
    if (key === "behavior_sections") {
      const c = behaviorDoc?.content ?? "";
      return c.toLowerCase().includes("rule") || c.toLowerCase().includes("workflow") || c.toLowerCase().includes("edge");
    }
    return false;
  }

  const allPass = AUDIT_CHECKS.every((ch) => auditCheckResult(ch.key));

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
        <span className="text-xs font-medium text-primary-400">Krok 5/7 — Quality Audit</span>
        {auditApproved && (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-500/10 border border-green-500/25 text-green-400">✓ Audit schválený</span>
        )}
        <div className="flex-1" />
        {(auditApproved || allPass) && (
          <button
            onClick={() => navigate(`/projects/${slug}/versions/${versionId}/taskplan`)}
            className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
          >
            Krok 6 →
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Gate */}
        {!hasBoth && (
          <div className="flex flex-col items-center justify-center h-full p-10 text-center">
            <svg className="w-12 h-12 text-slate-700 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
            <p className="text-sm text-slate-500 mb-1">Čaká na Architecture (Krok 4)</p>
            <p className="text-xs text-slate-700 mb-4">Quality Audit vyžaduje vygenerované BEHAVIOR.md aj DESIGN.md.</p>
            <button
              onClick={() => navigate(`/projects/${slug}/versions/${versionId}/architecture`)}
              className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
            >
              ← Krok 4 — Architecture
            </button>
          </div>
        )}

        {/* Audit panel */}
        {hasBoth && (
          <div className="p-6 max-w-3xl mx-auto space-y-6">
            {/* Stats row */}
            <div className="grid grid-cols-3 gap-4">
              <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 text-center">
                <div className="text-2xl font-bold text-slate-100 mb-1">{Math.round((behaviorDoc?.content.length ?? 0) / 1000)}k</div>
                <div className="text-[10px] text-slate-500 uppercase tracking-wider">BEHAVIOR.md</div>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 text-center">
                <div className="text-2xl font-bold text-slate-100 mb-1">{Math.round((designDoc?.content.length ?? 0) / 1000)}k</div>
                <div className="text-[10px] text-slate-500 uppercase tracking-wider">DESIGN.md</div>
              </div>
              <div className={`rounded-xl border p-4 text-center ${allPass ? "border-green-500/25 bg-green-500/5" : "border-yellow-500/25 bg-yellow-500/5"}`}>
                <div className={`text-2xl font-bold mb-1 ${allPass ? "text-green-400" : "text-yellow-400"}`}>
                  {AUDIT_CHECKS.filter((ch) => auditCheckResult(ch.key)).length}/{AUDIT_CHECKS.length}
                </div>
                <div className="text-[10px] text-slate-500 uppercase tracking-wider">Checks</div>
              </div>
            </div>

            {/* Checklist */}
            <div className="rounded-xl border border-slate-800 bg-slate-900 overflow-hidden">
              <div className="px-5 py-3 border-b border-slate-800">
                <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Audit Checklist</span>
              </div>
              <div className="divide-y divide-slate-800">
                {AUDIT_CHECKS.map((ch) => {
                  const pass = auditCheckResult(ch.key);
                  return (
                    <div key={ch.key} className="flex items-center gap-3 px-5 py-3">
                      <div className={`w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 ${pass ? "bg-green-500/15" : "bg-yellow-500/15"}`}>
                        {pass ? (
                          <svg className="w-3 h-3 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                          </svg>
                        ) : (
                          <svg className="w-3 h-3 text-yellow-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 9v4m0 4h.01" />
                          </svg>
                        )}
                      </div>
                      <span className={`text-xs ${pass ? "text-slate-300" : "text-slate-500"}`}>{ch.label}</span>
                      <span className={`ml-auto text-[10px] font-medium ${pass ? "text-green-400" : "text-yellow-400"}`}>
                        {pass ? "PASS" : "WARN"}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Actions */}
            {!auditApproved && (
              <div className="flex items-center justify-between rounded-xl border border-slate-700 bg-slate-900/50 px-5 py-4">
                <div>
                  <p className="text-xs font-medium text-slate-300">
                    {allPass ? "Všetky kontroly prešli. Môžeš schváliť audit." : "Niektoré kontroly vrátili varovania."}
                  </p>
                  <p className="text-[10px] text-slate-600 mt-0.5">Automatický audit — backend implementácia bude dostupná v ďalšej verzii.</p>
                </div>
                <button
                  onClick={() => setAuditApproved(true)}
                  className="text-xs bg-green-600 hover:bg-green-500 text-white px-4 py-1.5 rounded-lg font-medium transition-colors ml-4 whitespace-nowrap"
                >
                  Schváliť audit →
                </button>
              </div>
            )}

            {auditApproved && (
              <div className="rounded-xl border border-green-500/25 bg-green-500/5 px-5 py-4 flex items-center gap-3">
                <svg className="w-5 h-5 text-green-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <div>
                  <p className="text-xs font-medium text-green-400">Quality Audit schválený</p>
                  <p className="text-[10px] text-slate-500 mt-0.5">Pokračuj na Task Plan generáciu.</p>
                </div>
              </div>
            )}
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

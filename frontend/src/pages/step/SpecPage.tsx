import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import { getVersion } from "@/services/api/versions";
import {
  listRawSpecifications,
  createRawSpecification,
  updateRawSpecification,
  generateProfessionalSpec,
} from "@/services/api/rawSpecifications";
import { useAuthStore } from "@/store/authStore";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";
import { useActiveContextSync } from "@/hooks/useActiveContextSync";
import type { RawSpecificationRead } from "@/types/rawSpecification";

// ─── SpecPage — Step 1 ────────────────────────────────────────────────────────

export default function SpecPage() {
  const { slug, versionId } = useParams<{ slug: string; versionId: string }>();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [version, setVersion] = useState<Version | null>(null);
  const [rawSpec, setRawSpec] = useState<RawSpecificationRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Edit mode
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const [saving, setSaving] = useState(false);

  // Copy-to-clipboard feedback
  const [copiedKey, setCopiedKey] = useState<"view" | "edit" | null>(null);

  // Generation
  const [generating, setGenerating] = useState(false);
  const [genOutput, setGenOutput] = useState("");
  const [genDone, setGenDone] = useState(false);
  const [_genProfSpecId, setGenProfSpecId] = useState<string | null>(null);
  const [genError, setGenError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const outputRef = useRef<HTMLPreElement>(null);

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
      return listRawSpecifications({ project_id: proj.id, limit: 1 }).then((res) => {
        if (cancelled) return;
        setRawSpec(res.items[0] ?? null);
        setLoading(false);
      });
    }).catch(() => { if (!cancelled) { setError("Nepodarilo sa načítať dáta."); setLoading(false); } });
    return () => { cancelled = true; };
  }, [slug, versionId]);

  // auto-scroll streaming output
  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight;
  }, [genOutput]);

  async function handleSaveNew() {
    if (!project || !editText.trim()) return;
    setSaving(true);
    try {
      const spec = await createRawSpecification({
        project_id: project.id,
        input_text: editText,
        created_by: user?.id ?? "",
      });
      setRawSpec(spec);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveEdit() {
    if (!rawSpec || !editText.trim()) return;
    setSaving(true);
    try {
      const updated = await updateRawSpecification(rawSpec.id, { input_text: editText });
      setRawSpec(updated);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  function handleStartEdit() {
    setEditText(rawSpec?.input_text ?? "");
    setEditing(true);
  }

  async function handleCopy(text: string, key: "view" | "edit") {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey((k) => (k === key ? null : k)), 1500);
    } catch {
      // Clipboard API can fail in insecure contexts or without permission.
      // Fallback: select the textarea content so the user can Ctrl+C manually.
    }
  }

  function handleGenerate() {
    if (!rawSpec) return;
    setGenerating(true);
    setGenOutput("");
    setGenDone(false);
    setGenError("");
    abortRef.current = generateProfessionalSpec(
      rawSpec.id,
      (chunk) => setGenOutput((prev) => prev + chunk),
      (ev) => {
        setGenerating(false);
        setGenDone(true);
        setGenProfSpecId(ev.professional_spec_id ?? null);
        setRawSpec((prev) => prev ? { ...prev, status: "done" } : prev);
      },
      (err) => {
        setGenerating(false);
        setGenError(err.message);
      },
    );
  }

  function handleAbort() {
    abortRef.current?.abort();
    setGenerating(false);
  }

  if (loading) return <LoadingSpinner />;
  if (error || !project || !version) return <ErrorPanel msg={error} />;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex-shrink-0 bg-slate-900/50 border-b border-slate-800 px-5 py-2.5 flex items-center gap-3">
        <button
          onClick={() => navigate(`/projects/${slug}/versions/${versionId}`)}
          className="text-slate-500 hover:text-slate-300 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <span className="text-xs text-slate-400">{project.name}</span>
        <span className="text-slate-600">·</span>
        <span className="text-xs font-mono bg-slate-800 text-slate-300 px-2 py-0.5 rounded">{version.version_number}</span>
        <span className="text-slate-600">·</span>
        <span className="text-xs font-medium text-primary-400">Krok 1/7 — Zákaznícka špecifikácia</span>
        <div className="flex-1" />
        {rawSpec?.status === "done" && (
          <button
            onClick={() => navigate(`/projects/${slug}/versions/${versionId}/profspec`)}
            className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
          >
            Krok 2 →
          </button>
        )}
      </div>

      {/* Content — editing uses full screen; other states keep the narrower column */}
      {editing ? (
        <div className="flex-1 flex flex-col p-6 gap-3 overflow-hidden">
          <div className="flex items-center justify-between flex-shrink-0">
            <span className="text-sm font-semibold text-slate-300">
              {rawSpec ? "Upraviť špecifikáciu" : "Nová špecifikácia"}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => handleCopy(editText, "edit")}
                disabled={!editText.trim()}
                className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-300 border border-slate-700 hover:border-slate-500 disabled:opacity-40 px-3 py-1.5 rounded-lg transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
                {copiedKey === "edit" ? "Skopírované ✓" : "Kopírovať"}
              </button>
              <button
                onClick={() => setEditing(false)}
                className="text-xs text-slate-500 border border-slate-700 px-3 py-1.5 rounded-lg hover:border-slate-500 transition-colors"
              >
                Zrušiť
              </button>
              <button
                onClick={rawSpec ? handleSaveEdit : handleSaveNew}
                disabled={saving || !editText.trim()}
                className="text-xs bg-primary-600 hover:bg-primary-500 disabled:opacity-40 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
              >
                {saving ? "Ukladám…" : "Uložiť"}
              </button>
            </div>
          </div>
          <textarea
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            placeholder="Opíšte požiadavky zákazníka…&#10;&#10;Napr.:&#10;- Pridať funkciu exportu do PDF&#10;- Opraviť chybu v kalkulácii DPH&#10;- Zmeniť farebné schémy dashboardu"
            autoFocus
            className="flex-1 w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-xl text-sm text-slate-200 font-mono resize-none focus:outline-none focus:border-primary-500 transition-colors leading-relaxed"
          />
        </div>
      ) : (
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-3xl mx-auto">

          {/* Empty state */}
          {!rawSpec && (
            <div className="rounded-xl border border-dashed border-slate-700 p-10 text-center">
              <svg className="w-10 h-10 text-slate-700 mx-auto mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <p className="text-sm text-slate-500 mb-1">Zákaznícka špecifikácia</p>
              <p className="text-xs text-slate-700 mb-4">Opíš čo zákazník chce v tejto verzii. Môže to byť voľný text, odrážky alebo štruktúrovaný popis.</p>
              <button
                onClick={() => { setEditText(""); setEditing(true); }}
                className="flex items-center gap-1.5 mx-auto bg-primary-600 hover:bg-primary-500 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
                Zadať špecifikáciu
              </button>
            </div>
          )}

          {/* Spec exists — show it */}
          {rawSpec && (
            <div className="space-y-4">
              {/* Spec header */}
              <div className="flex items-center gap-3">
                <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium border ${
                  rawSpec.status === "done"
                    ? "bg-green-500/10 border-green-500/25 text-green-400"
                    : rawSpec.status === "processing"
                    ? "bg-yellow-500/15 border-yellow-500/30 text-yellow-400"
                    : "bg-slate-700/60 border-slate-600 text-slate-400"
                }`}>
                  {rawSpec.status === "done" ? "✓ Vygenerovaná" : rawSpec.status === "processing" ? "⟳ Spracováva sa" : "Čaká"}
                </span>
                <span className="text-[10px] text-slate-600 font-mono">{new Date(rawSpec.created_at).toLocaleDateString("sk-SK")}</span>
                <div className="flex-1" />
                <button
                  onClick={() => handleCopy(rawSpec.input_text, "view")}
                  className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 border border-slate-700 hover:border-slate-500 px-2 py-1 rounded transition-colors"
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                  </svg>
                  {copiedKey === "view" ? "Skopírované ✓" : "Kopírovať"}
                </button>
                <button
                  onClick={handleStartEdit}
                  className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 border border-slate-700 hover:border-slate-500 px-2 py-1 rounded transition-colors"
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                  </svg>
                  Upraviť
                </button>
              </div>

              {/* Spec content */}
              <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
                <pre className="text-sm text-slate-300 font-mono whitespace-pre-wrap leading-relaxed">{rawSpec.input_text}</pre>
              </div>

              {/* Generation section */}
              {!genDone && !generating && rawSpec.status !== "done" && (
                <div className="rounded-xl border border-slate-700 bg-slate-900 p-5 text-center">
                  <p className="text-sm text-slate-400 mb-1">Zákaznícka špecifikácia uložená</p>
                  <p className="text-xs text-slate-600 mb-4">Klikni na tlačidlo nižšie pre vygenerovanie vývojovej dokumentácie pomocou AI.</p>
                  <button
                    onClick={handleGenerate}
                    className="flex items-center gap-2 mx-auto bg-primary-600 hover:bg-primary-500 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                    </svg>
                    Generovať vývojovú dokumentáciu
                  </button>
                </div>
              )}

              {rawSpec.status === "done" && !generating && !genDone && (
                <div className="rounded-xl border border-green-500/20 bg-green-500/5 p-4 flex items-center justify-between">
                  <div>
                    <div className="text-sm font-medium text-green-400">Vývojová dokumentácia existuje</div>
                    <div className="text-xs text-slate-500 mt-0.5">Môžeš pokračovať na krok 2 alebo regenerovať.</div>
                  </div>
                  <div className="flex gap-2">
                    <button onClick={handleGenerate} className="text-xs text-slate-400 border border-slate-700 px-3 py-1.5 rounded-lg hover:border-slate-500 transition-colors">
                      Regenerovať
                    </button>
                    <button
                      onClick={() => navigate(`/projects/${slug}/versions/${versionId}/profspec`)}
                      className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
                    >
                      Krok 2 →
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Generating */}
          {generating && (
            <div className="space-y-3 mt-4">
              <div className="flex items-center gap-2 text-sm text-slate-400">
                <svg className="w-4 h-4 animate-spin text-primary-400" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Generujem vývojovú dokumentáciu…
                <button onClick={handleAbort} className="ml-auto text-xs text-slate-600 hover:text-red-400 transition-colors">Zastaviť</button>
              </div>
              <pre
                ref={outputRef}
                className="rounded-xl border border-slate-800 bg-slate-950 p-4 text-xs text-slate-300 font-mono whitespace-pre-wrap leading-relaxed overflow-y-auto max-h-96"
              >{genOutput}<span className="inline-block w-0.5 h-3.5 bg-primary-400 animate-pulse ml-0.5 align-bottom" /></pre>
            </div>
          )}

          {/* Done */}
          {genDone && !generating && (
            <div className="space-y-3 mt-4">
              <div className="rounded-xl border border-green-500/25 bg-green-500/5 p-4 flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm text-green-400">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  Vývojová dokumentácia vygenerovaná
                </div>
                <button
                  onClick={() => navigate(`/projects/${slug}/versions/${versionId}/profspec`)}
                  className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
                >
                  Otvoriť Krok 2 →
                </button>
              </div>
              <pre className="rounded-xl border border-slate-800 bg-slate-950 p-4 text-xs text-slate-300 font-mono whitespace-pre-wrap leading-relaxed overflow-y-auto max-h-96">
                {genOutput}
              </pre>
            </div>
          )}

          {genError && (
            <div className="mt-3 rounded-lg bg-red-500/10 border border-red-500/20 p-3 text-xs text-red-400">{genError}</div>
          )}
        </div>
      </div>
      )}
    </div>
  );
}

// ─── Shared helpers ───────────────────────────────────────────────────────────

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

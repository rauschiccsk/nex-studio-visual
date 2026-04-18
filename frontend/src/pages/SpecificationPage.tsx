/**
 * SpecificationPage — 3-step specification pipeline.
 *
 * Step 1 — Raw Spec: customer pastes free-form text → saved as RawSpecification
 * Step 2 — Professional Spec: AI generates from raw spec (SSE streaming) →
 *           user edits → saves as ProfessionalSpecification
 * Step 3 — Documents: AI generates DESIGN.md / BEHAVIOR.md (SSE streaming) →
 *           user edits → saves as DesignDocument
 *
 * Route: ``projects/:slug/spec`` (child of ProjectLayout)
 * Project is received via React Router outlet context — no duplicate fetch.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { CheckCircle, ChevronDown, ChevronRight, Loader2, Wand2 } from "lucide-react";

import { useAuthStore } from "@/store/authStore";
import type { ProjectLayoutContext } from "./ProjectPage";
import type { RawSpecificationRead } from "@/types/rawSpecification";
import type { ProfessionalSpecificationRead } from "@/types/professionalSpecification";
import type { DesignDocumentRead } from "@/types/designDocument";
import {
  listRawSpecifications,
  createRawSpecification,
  generateProfessionalSpec,
} from "@/services/api/rawSpecifications";
import {
  listProfessionalSpecs,
  createProfessionalSpec,
  generateDesignDoc,
} from "@/services/api/professionalSpecifications";
import {
  listDesignDocuments,
} from "@/services/api/designDocuments";

// ── Helpers ──────────────────────────────────────────────────────────────────

function StepHeader({
  number,
  title,
  done,
  open,
  onToggle,
}: {
  number: number;
  title: string;
  done: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      className="flex w-full items-center gap-3 rounded-lg border border-gray-700 bg-gray-800 px-4 py-3 text-left transition-colors hover:bg-gray-750"
    >
      <span
        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
          done
            ? "bg-green-500/20 text-green-400"
            : "bg-primary/20 text-primary"
        }`}
      >
        {done ? <CheckCircle className="h-4 w-4" /> : number}
      </span>
      <span className="flex-1 font-medium text-gray-100">{title}</span>
      {open ? (
        <ChevronDown className="h-4 w-4 text-gray-400" />
      ) : (
        <ChevronRight className="h-4 w-4 text-gray-400" />
      )}
    </button>
  );
}

function StreamingTextarea({
  value,
  onChange,
  placeholder,
  rows,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  rows?: number;
  disabled?: boolean;
}) {
  return (
    <textarea
      className="w-full resize-y rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 font-mono text-xs text-gray-200 placeholder-gray-500 focus:border-primary focus:outline-none disabled:opacity-50"
      rows={rows ?? 12}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
    />
  );
}

// ── Component ────────────────────────────────────────────────────────────────

type DocKey = "design" | "behavior";

interface GenDocState {
  streaming: boolean;
  content: string;
  savedDoc: DesignDocumentRead | null;
  error: string | null;
}

function SpecificationPage() {
  const { project } = useOutletContext<ProjectLayoutContext>();
  const userId = useAuthStore((s) => s.user?.id);

  // ── Step open/close ─────────────────────────────────────────────────────
  const [openSteps, setOpenSteps] = useState<Record<number, boolean>>({
    1: true,
    2: false,
    3: false,
  });
  const toggleStep = (n: number) =>
    setOpenSteps((prev) => ({ ...prev, [n]: !prev[n] }));

  // ── Step 1: Raw Spec ────────────────────────────────────────────────────
  const [rawText, setRawText] = useState("");
  const [rawSpec, setRawSpec] = useState<RawSpecificationRead | null>(null);
  const [rawSaving, setRawSaving] = useState(false);
  const [rawError, setRawError] = useState<string | null>(null);

  // ── Step 2: Professional Spec ───────────────────────────────────────────
  const [profStreaming, setProfStreaming] = useState(false);
  const [profContent, setProfContent] = useState("");
  const [profSpec, setProfSpec] = useState<ProfessionalSpecificationRead | null>(null);
  const [profSaving, setProfSaving] = useState(false);
  const [profError, setProfError] = useState<string | null>(null);
  const profAbortRef = useRef<AbortController | null>(null);

  // ── Step 3: Design Documents ────────────────────────────────────────────
  const [designDoc, setDesignDoc] = useState<GenDocState>({
    streaming: false,
    content: "",
    savedDoc: null,
    error: null,
  });
  const [behaviorDoc, setBehaviorDoc] = useState<GenDocState>({
    streaming: false,
    content: "",
    savedDoc: null,
    error: null,
  });
  const designAbortRef = useRef<AbortController | null>(null);
  const behaviorAbortRef = useRef<AbortController | null>(null);

  // ── Scroll refs for auto-scroll during streaming ───────────────────────
  const profTextRef = useRef<HTMLTextAreaElement | null>(null);
  const designTextRef = useRef<HTMLTextAreaElement | null>(null);
  const behaviorTextRef = useRef<HTMLTextAreaElement | null>(null);

  // ── Load existing data on mount ─────────────────────────────────────────
  useEffect(() => {
    // Load latest raw spec for this project
    listRawSpecifications({ project_id: project.id, limit: 1 })
      .then((res) => {
        const latest = res.items[0];
        if (latest) {
          setRawSpec(latest);
          setRawText(latest.input_text);
          setOpenSteps((p) => ({ ...p, 1: false, 2: true }));
        }
      })
      .catch(() => {/* ignore */});

    // Load latest professional spec
    listProfessionalSpecs({ project_id: project.id, limit: 1 })
      .then((res) => {
        const latest = res.items[0];
        if (latest) {
          setProfSpec(latest);
          setProfContent(latest.content);
          setOpenSteps((p) => ({ ...p, 2: false, 3: true }));
        }
      })
      .catch(() => {/* ignore */});

    // Load existing design docs
    listDesignDocuments({ project_id: project.id, doc_type: "design", limit: 1 })
      .then((res) => {
        const d = res.items[0];
        if (d) setDesignDoc((p) => ({ ...p, content: d.content, savedDoc: d }));
      })
      .catch(() => {/* ignore */});

    listDesignDocuments({ project_id: project.id, doc_type: "behavior", limit: 1 })
      .then((res) => {
        const d = res.items[0];
        if (d) setBehaviorDoc((p) => ({ ...p, content: d.content, savedDoc: d }));
      })
      .catch(() => {/* ignore */});
  }, [project.id]);

  // ── Step 1: Save raw spec ───────────────────────────────────────────────
  const handleSaveRaw = useCallback(async () => {
    if (!rawText.trim() || !userId) return;
    setRawSaving(true);
    setRawError(null);
    try {
      const created = await createRawSpecification({
        project_id: project.id,
        input_text: rawText,
        created_by: userId,
      });
      setRawSpec(created);
      setOpenSteps((p) => ({ ...p, 1: false, 2: true }));
    } catch (err) {
      setRawError(err instanceof Error ? err.message : "Uloženie zlyhalo");
    } finally {
      setRawSaving(false);
    }
  }, [rawText, userId, project.id]);

  // ── Step 2: Generate professional spec ─────────────────────────────────
  const handleGenerate = useCallback(() => {
    if (!rawSpec) return;
    setProfStreaming(true);
    setProfContent("");
    setProfError(null);

    const ctrl = generateProfessionalSpec(
      rawSpec.id,
      (chunk) => {
        setProfContent((prev) => {
          const next = prev + chunk;
          // Auto-scroll textarea
          if (profTextRef.current) {
            profTextRef.current.scrollTop = profTextRef.current.scrollHeight;
          }
          return next;
        });
      },
      (event) => {
        setProfStreaming(false);
        if (event.professional_spec_id) {
          // Refresh from server
          listProfessionalSpecs({ project_id: project.id, limit: 1 })
            .then((res) => {
              const latest = res.items[0];
              if (latest) setProfSpec(latest);
            })
            .catch(() => {/* ignore */});
        }
      },
      (err) => {
        setProfStreaming(false);
        setProfError(err.message);
      },
    );
    profAbortRef.current = ctrl;
  }, [rawSpec, project.id]);

  const handleStopGenerate = () => {
    profAbortRef.current?.abort();
    setProfStreaming(false);
  };

  // ── Step 2: Save professional spec ─────────────────────────────────────
  const handleSaveProf = useCallback(async () => {
    if (!profContent.trim() || !rawSpec) return;
    setProfSaving(true);
    setProfError(null);
    try {
      const created = await createProfessionalSpec({
        project_id: project.id,
        raw_spec_id: rawSpec.id,
        content: profContent,
        version: (profSpec?.version ?? 0) + 1,
      });
      setProfSpec(created);
      setOpenSteps((p) => ({ ...p, 2: false, 3: true }));
    } catch (err) {
      setProfError(err instanceof Error ? err.message : "Uloženie zlyhalo");
    } finally {
      setProfSaving(false);
    }
  }, [profContent, rawSpec, profSpec, project.id]);

  // ── Step 3: Generate design/behavior doc ───────────────────────────────
  const handleGenerateDoc = useCallback(
    (docType: DocKey) => {
      if (!profSpec) return;

      const setDoc = docType === "design" ? setDesignDoc : setBehaviorDoc;
      const abortRef = docType === "design" ? designAbortRef : behaviorAbortRef;
      const textRef = docType === "design" ? designTextRef : behaviorTextRef;

      setDoc((p) => ({ ...p, streaming: true, content: "", error: null }));

      const ctrl = generateDesignDoc(
        profSpec.id,
        docType,
        (chunk) => {
          setDoc((p) => {
            const next = p.content + chunk;
            if (textRef.current) {
              textRef.current.scrollTop = textRef.current.scrollHeight;
            }
            return { ...p, content: next };
          });
        },
        (event) => {
          setDoc((p) => ({ ...p, streaming: false }));
          if (event.design_doc_id) {
            // Refresh saved doc
            listDesignDocuments({ project_id: project.id, doc_type: docType, limit: 1 })
              .then((res) => {
                const d = res.items[0];
                if (d) setDoc((p) => ({ ...p, savedDoc: d }));
              })
              .catch(() => {/* ignore */});
          }
        },
        (err) => {
          setDoc((p) => ({ ...p, streaming: false, error: err.message }));
        },
      );
      abortRef.current = ctrl;
    },
    [profSpec, project.id],
  );

  const handleStopDoc = (docType: DocKey) => {
    if (docType === "design") {
      designAbortRef.current?.abort();
      setDesignDoc((p) => ({ ...p, streaming: false }));
    } else {
      behaviorAbortRef.current?.abort();
      setBehaviorDoc((p) => ({ ...p, streaming: false }));
    }
  };

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="space-y-3">
      {/* ── STEP 1: Raw Spec ── */}
      <div>
        <StepHeader
          number={1}
          title="Surová špecifikácia"
          done={rawSpec !== null}
          open={openSteps[1] ?? true}
          onToggle={() => toggleStep(1)}
        />
        {openSteps[1] && (
          <div className="mt-2 space-y-3 rounded-b-lg border border-t-0 border-gray-700 bg-gray-850 px-4 pb-4 pt-3">
            <p className="text-xs text-gray-400">
              Vlož zákaznícku špecifikáciu — voľný text, akokoľvek neformálny.
              AI ho transformuje na profesionálnu štruktúrovanú špecifikáciu.
            </p>
            <StreamingTextarea
              value={rawText}
              onChange={setRawText}
              placeholder="Zákazník potrebuje systém na evidenciu skladu…"
              rows={10}
            />
            {rawError && (
              <p className="text-xs text-red-400">{rawError}</p>
            )}
            <div className="flex items-center gap-3">
              <button
                onClick={handleSaveRaw}
                disabled={rawSaving || !rawText.trim()}
                className="rounded-lg bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-40"
              >
                {rawSaving ? "Ukladám…" : rawSpec ? "Prepísať" : "Uložiť"}
              </button>
              {rawSpec && (
                <span className="flex items-center gap-1 text-xs text-green-400">
                  <CheckCircle className="h-3.5 w-3.5" />
                  Uložená — {new Date(rawSpec.created_at).toLocaleDateString("sk-SK")}
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── STEP 2: Professional Spec ── */}
      <div>
        <StepHeader
          number={2}
          title="Profesionálna špecifikácia"
          done={profSpec !== null}
          open={openSteps[2] ?? false}
          onToggle={() => toggleStep(2)}
        />
        {openSteps[2] && (
          <div className="mt-2 space-y-3 rounded-b-lg border border-t-0 border-gray-700 bg-gray-850 px-4 pb-4 pt-3">
            <p className="text-xs text-gray-400">
              AI vygeneruje profesionálnu špecifikáciu podľa ICC šablóny.
              Po vygenerovaní môžeš obsah upraviť a uložiť.
            </p>

            {!rawSpec && (
              <p className="text-xs text-yellow-400">
                Najskôr ulož surovú špecifikáciu (krok 1).
              </p>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={profStreaming ? handleStopGenerate : handleGenerate}
                disabled={!rawSpec}
                className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
              >
                {profStreaming ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Zastaviť
                  </>
                ) : (
                  <>
                    <Wand2 className="h-3.5 w-3.5" />
                    Generovať
                  </>
                )}
              </button>

              <button
                onClick={handleSaveProf}
                disabled={profSaving || !profContent.trim() || !rawSpec}
                className="rounded-lg bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-40"
              >
                {profSaving ? "Ukladám…" : profSpec ? "Prepísať" : "Schváliť a uložiť"}
              </button>

              {profSpec && (
                <span className="flex items-center gap-1 text-xs text-green-400">
                  <CheckCircle className="h-3.5 w-3.5" />
                  v{profSpec.version} — {new Date(profSpec.created_at).toLocaleDateString("sk-SK")}
                </span>
              )}
            </div>

            <textarea
              ref={profTextRef}
              className="w-full resize-y rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 font-mono text-xs text-gray-200 placeholder-gray-500 focus:border-primary focus:outline-none"
              rows={20}
              value={profContent}
              onChange={(e) => setProfContent(e.target.value)}
              placeholder="Tu sa objaví vygenerovaná profesionálna špecifikácia…"
            />

            {profError && (
              <p className="text-xs text-red-400">{profError}</p>
            )}
          </div>
        )}
      </div>

      {/* ── STEP 3: Documents ── */}
      <div>
        <StepHeader
          number={3}
          title="Technické dokumenty (DESIGN.md / BEHAVIOR.md)"
          done={designDoc.savedDoc !== null || behaviorDoc.savedDoc !== null}
          open={openSteps[3] ?? false}
          onToggle={() => toggleStep(3)}
        />
        {openSteps[3] && (
          <div className="mt-2 space-y-6 rounded-b-lg border border-t-0 border-gray-700 bg-gray-850 px-4 pb-4 pt-3">
            {!profSpec && (
              <p className="text-xs text-yellow-400">
                Najskôr ulož profesionálnu špecifikáciu (krok 2).
              </p>
            )}

            {(["design", "behavior"] as DocKey[]).map((docType) => {
              const state = docType === "design" ? designDoc : behaviorDoc;
              const setState = docType === "design" ? setDesignDoc : setBehaviorDoc;
              const textRef = docType === "design" ? designTextRef : behaviorTextRef;
              const label = docType === "design" ? "DESIGN.md" : "BEHAVIOR.md";

              return (
                <div key={docType} className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm font-semibold text-gray-300">
                      {label}
                    </span>
                    <button
                      onClick={() =>
                        state.streaming
                          ? handleStopDoc(docType)
                          : handleGenerateDoc(docType)
                      }
                      disabled={!profSpec}
                      className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
                    >
                      {state.streaming ? (
                        <>
                          <Loader2 className="h-3 w-3 animate-spin" />
                          Zastaviť
                        </>
                      ) : (
                        <>
                          <Wand2 className="h-3 w-3" />
                          Generovať
                        </>
                      )}
                    </button>
                    {state.savedDoc && (
                      <span className="flex items-center gap-1 text-xs text-green-400">
                        <CheckCircle className="h-3 w-3" />
                        Uložený — {new Date(state.savedDoc.created_at).toLocaleDateString("sk-SK")}
                      </span>
                    )}
                  </div>

                  <textarea
                    ref={textRef}
                    className="w-full resize-y rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 font-mono text-xs text-gray-200 placeholder-gray-500 focus:border-primary focus:outline-none"
                    rows={16}
                    value={state.content}
                    onChange={(e) => setState((p) => ({ ...p, content: e.target.value }))}
                    placeholder={`Tu sa objaví vygenerovaný ${label}…`}
                  />

                  {state.error && (
                    <p className="text-xs text-red-400">{state.error}</p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

export default SpecificationPage;

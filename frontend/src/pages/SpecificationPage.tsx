/**
 * SpecificationPage — 3-step specification pipeline.
 *
 * Step 1 — Raw Spec: customer pastes free-form text → saved as RawSpecification
 * Step 2 — Professional Spec: AI generates initial draft (SSE), then user
 *           refines via chat dialog (split view: chat left, spec editor right)
 * Step 3 — Documents: AI generates DESIGN.md / BEHAVIOR.md (SSE streaming)
 *
 * Route: ``projects/:slug/spec`` (child of ProjectLayout)
 * Project is received via React Router outlet context.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import {
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Loader2,
  Send,
  Wand2,
} from "lucide-react";

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
  updateProfessionalSpec,
  chatProfessionalSpec,
  generateDesignDoc,
} from "@/services/api/professionalSpecifications";
import { listDesignDocuments } from "@/services/api/designDocuments";

// ── Types ────────────────────────────────────────────────────────────────────

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
}

type DocKey = "design" | "behavior";

interface GenDocState {
  streaming: boolean;
  content: string;
  savedDoc: DesignDocumentRead | null;
  error: string | null;
}

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

// ── Component ────────────────────────────────────────────────────────────────

function SpecificationPage() {
  const { project } = useOutletContext<ProjectLayoutContext>();
  const userId = useAuthStore((s) => s.user?.id);

  // ── Step open/close ────────────────────────────────────────────────────
  const [openSteps, setOpenSteps] = useState<Record<number, boolean>>({
    1: true,
    2: false,
    3: false,
  });
  const toggleStep = (n: number) =>
    setOpenSteps((prev) => ({ ...prev, [n]: !prev[n] }));

  // ── Step 1: Raw Spec ───────────────────────────────────────────────────
  const [rawText, setRawText] = useState("");
  const [rawSpec, setRawSpec] = useState<RawSpecificationRead | null>(null);
  const [rawSaving, setRawSaving] = useState(false);
  const [rawError, setRawError] = useState<string | null>(null);

  // ── Step 2: Professional Spec (chat + spec editor) ─────────────────────
  const [profSpec, setProfSpec] = useState<ProfessionalSpecificationRead | null>(null);
  const [profContent, setProfContent] = useState("");
  const [profGenerating, setProfGenerating] = useState(false); // initial SSE generate
  const [profSaving, setProfSaving] = useState(false);
  const [profError, setProfError] = useState<string | null>(null);

  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatStreaming, setChatStreaming] = useState(false);

  const profGenAbortRef = useRef<AbortController | null>(null);
  const chatAbortRef = useRef<AbortController | null>(null);
  const chatBottomRef = useRef<HTMLDivElement | null>(null);
  const specTextRef = useRef<HTMLTextAreaElement | null>(null);

  // ── Step 3: Design Documents ───────────────────────────────────────────
  const [designDoc, setDesignDoc] = useState<GenDocState>({
    streaming: false, content: "", savedDoc: null, error: null,
  });
  const [behaviorDoc, setBehaviorDoc] = useState<GenDocState>({
    streaming: false, content: "", savedDoc: null, error: null,
  });
  const designAbortRef = useRef<AbortController | null>(null);
  const behaviorAbortRef = useRef<AbortController | null>(null);
  const designTextRef = useRef<HTMLTextAreaElement | null>(null);
  const behaviorTextRef = useRef<HTMLTextAreaElement | null>(null);

  // ── Auto-scroll chat to bottom ─────────────────────────────────────────
  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  // ── Load existing data on mount ────────────────────────────────────────
  useEffect(() => {
    listRawSpecifications({ project_id: project.id, limit: 1 })
      .then((res) => {
        const latest = res.items[0];
        if (latest) {
          setRawSpec(latest);
          setRawText(latest.input_text);
          setOpenSteps((p) => ({ ...p, 1: false, 2: true }));
        }
      })
      .catch(() => { /* ignore */ });

    listProfessionalSpecs({ project_id: project.id, limit: 1 })
      .then((res) => {
        const latest = res.items[0];
        if (latest) {
          setProfSpec(latest);
          setProfContent(latest.content);
          setChatMessages([
            {
              role: "assistant",
              content:
                "Profesionálna špecifikácia je načítaná. Čo chceš doplniť alebo upraviť?",
            },
          ]);
          setOpenSteps((p) => ({ ...p, 2: false, 3: true }));
        }
      })
      .catch(() => { /* ignore */ });

    listDesignDocuments({ project_id: project.id, doc_type: "design", limit: 1 })
      .then((res) => {
        const d = res.items[0];
        if (d) setDesignDoc((p) => ({ ...p, content: d.content, savedDoc: d }));
      })
      .catch(() => { /* ignore */ });

    listDesignDocuments({ project_id: project.id, doc_type: "behavior", limit: 1 })
      .then((res) => {
        const d = res.items[0];
        if (d) setBehaviorDoc((p) => ({ ...p, content: d.content, savedDoc: d }));
      })
      .catch(() => { /* ignore */ });
  }, [project.id]);

  // ── Step 1: Save raw spec ──────────────────────────────────────────────
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

  // ── Step 2: Initial generate (one-shot) ───────────────────────────────
  const handleGenerate = useCallback(() => {
    if (!rawSpec) return;
    setProfGenerating(true);
    setProfContent("");
    setProfError(null);
    setChatMessages([
      { role: "assistant", content: "Generujem prvý draft profesionálnej špecifikácie…", streaming: true },
    ]);

    const ctrl = generateProfessionalSpec(
      rawSpec.id,
      (chunk) => {
        setProfContent((prev) => {
          const next = prev + chunk;
          if (specTextRef.current) {
            specTextRef.current.scrollTop = specTextRef.current.scrollHeight;
          }
          return next;
        });
      },
      (event) => {
        setProfGenerating(false);
        setChatMessages([
          {
            role: "assistant",
            content:
              "Prvý draft vygenerovaný ✓ Skontroluj obsah špecifikácie a povedz mi čo treba doplniť alebo upraviť.",
          },
        ]);
        if (event.professional_spec_id) {
          listProfessionalSpecs({ project_id: project.id, limit: 1 })
            .then((res) => {
              const latest = res.items[0];
              if (latest) setProfSpec(latest);
            })
            .catch(() => { /* ignore */ });
        }
      },
      (err) => {
        setProfGenerating(false);
        setProfError(err.message);
        setChatMessages((prev) => prev.filter((m) => !m.streaming));
      },
    );
    profGenAbortRef.current = ctrl;
  }, [rawSpec, project.id]);

  const handleStopGenerate = () => {
    profGenAbortRef.current?.abort();
    setProfGenerating(false);
    setChatMessages((prev) => prev.filter((m) => !m.streaming));
  };

  // ── Step 2: Chat send ──────────────────────────────────────────────────
  const handleChatSend = useCallback(() => {
    const msg = chatInput.trim();
    if (!msg || !profSpec || chatStreaming || profGenerating) return;
    setChatInput("");
    setChatStreaming(true);

    // Append user message + empty AI placeholder
    const historyForBackend: { role: "user" | "assistant"; content: string }[] =
      chatMessages
        .filter((m) => !m.streaming)
        .map((m) => ({ role: m.role, content: m.content }));

    setChatMessages((prev) => [
      ...prev.filter((m) => !m.streaming),
      { role: "user", content: msg },
      { role: "assistant", content: "", streaming: true },
    ]);

    // Reset spec for new version
    setProfContent("");

    const ctrl = chatProfessionalSpec(
      profSpec.id,
      msg,
      profContent,
      historyForBackend,
      // onChatChunk
      (chunk) => {
        setChatMessages((prev) => {
          const copy = [...prev];
          const last = copy[copy.length - 1];
          if (last?.streaming) {
            copy[copy.length - 1] = { ...last, content: last.content + chunk };
          }
          return copy;
        });
      },
      // onSpecChunk
      (chunk) => {
        setProfContent((prev) => {
          const next = prev + chunk;
          if (specTextRef.current) {
            specTextRef.current.scrollTop = specTextRef.current.scrollHeight;
          }
          return next;
        });
      },
      // onDone
      () => {
        setChatStreaming(false);
        setChatMessages((prev) =>
          prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
        );
      },
      // onError
      (err) => {
        setChatStreaming(false);
        setChatMessages((prev) =>
          prev.map((m) =>
            m.streaming
              ? { ...m, content: `Chyba: ${err.message}`, streaming: false }
              : m,
          ),
        );
        setProfError(err.message);
      },
    );
    chatAbortRef.current = ctrl;
  }, [chatInput, profSpec, chatMessages, chatStreaming, profGenerating, profContent]);

  const handleChatKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleChatSend();
    }
  };

  const handleStopChat = () => {
    chatAbortRef.current?.abort();
    setChatStreaming(false);
    setChatMessages((prev) =>
      prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
    );
  };

  // ── Step 2: Save professional spec ────────────────────────────────────
  const handleSaveProf = useCallback(async () => {
    if (!profContent.trim() || !rawSpec) return;
    setProfSaving(true);
    setProfError(null);
    try {
      if (profSpec) {
        // Update existing (save refined content)
        const updated = await updateProfessionalSpec(profSpec.id, {
          content: profContent,
          version: profSpec.version + 1,
        });
        setProfSpec(updated);
      } else {
        const created = await createProfessionalSpec({
          project_id: project.id,
          raw_spec_id: rawSpec.id,
          content: profContent,
          version: 1,
        });
        setProfSpec(created);
      }
      setOpenSteps((p) => ({ ...p, 2: false, 3: true }));
    } catch (err) {
      setProfError(err instanceof Error ? err.message : "Uloženie zlyhalo");
    } finally {
      setProfSaving(false);
    }
  }, [profContent, rawSpec, profSpec, project.id]);

  // ── Step 3: Generate design/behavior doc ──────────────────────────────
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
            if (textRef.current) textRef.current.scrollTop = textRef.current.scrollHeight;
            return { ...p, content: next };
          });
        },
        (event) => {
          setDoc((p) => ({ ...p, streaming: false }));
          if (event.design_doc_id) {
            listDesignDocuments({ project_id: project.id, doc_type: docType, limit: 1 })
              .then((res) => {
                const d = res.items[0];
                if (d) setDoc((p) => ({ ...p, savedDoc: d }));
              })
              .catch(() => { /* ignore */ });
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

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <div className="space-y-3">

      {/* ══ STEP 1: Raw Spec ══════════════════════════════════════════════ */}
      <div>
        <StepHeader
          number={1}
          title="Surová špecifikácia"
          done={rawSpec !== null}
          open={openSteps[1] ?? true}
          onToggle={() => toggleStep(1)}
        />
        {openSteps[1] && (
          <div className="mt-2 space-y-3 rounded-b-lg border border-t-0 border-gray-700 bg-gray-900/50 px-4 pb-4 pt-3">
            <p className="text-xs text-gray-400">
              Vlož zákaznícku špecifikáciu — voľný text, akokoľvek neformálny.
              AI ho transformuje na profesionálnu štruktúrovanú špecifikáciu.
            </p>
            <textarea
              className="w-full resize-y rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 font-mono text-xs text-gray-200 placeholder-gray-500 focus:border-primary focus:outline-none"
              rows={10}
              value={rawText}
              onChange={(e) => setRawText(e.target.value)}
              placeholder="Zákazník potrebuje systém na evidenciu skladu…"
            />
            {rawError && <p className="text-xs text-red-400">{rawError}</p>}
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

      {/* ══ STEP 2: Professional Spec (split: chat | spec) ════════════════ */}
      <div>
        <StepHeader
          number={2}
          title="Profesionálna špecifikácia"
          done={profSpec !== null}
          open={openSteps[2] ?? false}
          onToggle={() => toggleStep(2)}
        />
        {openSteps[2] && (
          <div className="mt-2 rounded-b-lg border border-t-0 border-gray-700 bg-gray-900/50 px-4 pb-4 pt-3">

            {/* Top action bar */}
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <button
                onClick={profGenerating ? handleStopGenerate : handleGenerate}
                disabled={!rawSpec || chatStreaming}
                className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
              >
                {profGenerating ? (
                  <><Loader2 className="h-3.5 w-3.5 animate-spin" />Zastaviť</>
                ) : (
                  <><Wand2 className="h-3.5 w-3.5" />Generovať prvý draft</>
                )}
              </button>

              <button
                onClick={handleSaveProf}
                disabled={profSaving || !profContent.trim() || !rawSpec}
                className="rounded-lg bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-40"
              >
                {profSaving ? "Ukladám…" : "Schváliť a uložiť"}
              </button>

              {profSpec && (
                <span className="flex items-center gap-1 text-xs text-green-400">
                  <CheckCircle className="h-3.5 w-3.5" />
                  v{profSpec.version} — {new Date(profSpec.updated_at).toLocaleDateString("sk-SK")}
                </span>
              )}
              {profError && <span className="text-xs text-red-400">{profError}</span>}
            </div>

            {!rawSpec && (
              <p className="mb-3 text-xs text-yellow-400">
                Najskôr ulož surovú špecifikáciu (krok 1).
              </p>
            )}

            {/* Split view — CSS Grid: reliable height without flex quirks */}
            <div
              className="grid gap-3"
              style={{ height: "560px", gridTemplateColumns: "2fr 3fr" }}
            >
              {/* ── Left: Chat panel ── */}
              <div className="flex flex-col overflow-hidden rounded-lg border border-gray-700 bg-gray-900">
                {/* Messages — scrollable */}
                <div className="flex-1 overflow-y-auto p-3 space-y-3">
                  {chatMessages.length === 0 && (
                    <p className="text-center text-xs text-gray-500 mt-8">
                      Klikni „Generovať prvý draft" pre začatie.
                    </p>
                  )}
                  {chatMessages.map((msg, i) => (
                    <div
                      key={i}
                      className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                    >
                      <div
                        className={`max-w-[90%] rounded-lg px-3 py-2 text-xs leading-relaxed ${
                          msg.role === "user"
                            ? "bg-primary/25 text-gray-100"
                            : "bg-gray-700 text-gray-200"
                        }`}
                      >
                        {msg.content || (msg.streaming ? <Loader2 className="h-3 w-3 animate-spin inline" /> : "")}
                      </div>
                    </div>
                  ))}
                  <div ref={chatBottomRef} />
                </div>

                {/* Input — fixed at bottom */}
                <div className="shrink-0 border-t border-gray-700 p-2">
                  <div className="flex gap-2">
                    <textarea
                      className="flex-1 resize-none rounded-lg border border-gray-600 bg-gray-800 px-2 py-1.5 text-xs text-gray-200 placeholder-gray-500 focus:border-primary focus:outline-none"
                      rows={2}
                      value={chatInput}
                      onChange={(e) => setChatInput(e.target.value)}
                      onKeyDown={handleChatKeyDown}
                      placeholder="Napíš čo treba doplniť… (Enter = odoslať)"
                      disabled={!profSpec || profGenerating}
                    />
                    <button
                      onClick={chatStreaming ? handleStopChat : handleChatSend}
                      disabled={(!chatInput.trim() && !chatStreaming) || !profSpec || profGenerating}
                      className="flex items-center justify-center rounded-lg bg-indigo-600 px-2 text-white hover:bg-indigo-500 disabled:opacity-40"
                    >
                      {chatStreaming
                        ? <Loader2 className="h-4 w-4 animate-spin" />
                        : <Send className="h-4 w-4" />}
                    </button>
                  </div>
                  <p className="mt-1 text-right text-[10px] text-gray-500">
                    Enter = odoslať · Shift+Enter = nový riadok
                  </p>
                </div>
              </div>

              {/* ── Right: Spec editor — direct grid item, h-full fills the 560px cell ── */}
              <textarea
                ref={specTextRef}
                className="h-full w-full resize-none rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 font-mono text-xs text-gray-200 placeholder-gray-500 focus:border-primary focus:outline-none"
                value={profContent}
                onChange={(e) => setProfContent(e.target.value)}
                placeholder="Tu sa objaví vygenerovaná profesionálna špecifikácia…"
              />
            </div>
            <p className="text-right text-[10px] text-gray-500">
              Môžeš editovať priamo. Po úpravách klikni „Schváliť a uložiť".
            </p>
          </div>
        )}
      </div>

      {/* ══ STEP 3: DESIGN.md / BEHAVIOR.md ══════════════════════════════ */}
      <div>
        <StepHeader
          number={3}
          title="Technické dokumenty (DESIGN.md / BEHAVIOR.md)"
          done={designDoc.savedDoc !== null || behaviorDoc.savedDoc !== null}
          open={openSteps[3] ?? false}
          onToggle={() => toggleStep(3)}
        />
        {openSteps[3] && (
          <div className="mt-2 space-y-6 rounded-b-lg border border-t-0 border-gray-700 bg-gray-900/50 px-4 pb-4 pt-3">
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
                        state.streaming ? handleStopDoc(docType) : handleGenerateDoc(docType)
                      }
                      disabled={!profSpec}
                      className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
                    >
                      {state.streaming ? (
                        <><Loader2 className="h-3 w-3 animate-spin" />Zastaviť</>
                      ) : (
                        <><Wand2 className="h-3 w-3" />Generovať</>
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
                  {state.error && <p className="text-xs text-red-400">{state.error}</p>}
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

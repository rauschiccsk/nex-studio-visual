import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import { getVersion } from "@/services/api/versions";
import { listProfessionalSpecs } from "@/services/api/professionalSpecifications";
import {
  listUIDesigns,
  createUIDesign,
  updateUIDesign,
  chatUIDesign,
  generateUIDesign,
} from "@/services/api/uiDesigns";
import type { UIDesignSSEEvent, ChatHistoryItem } from "@/services/api/uiDesigns";
import { useAuthStore } from "@/store/authStore";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";
import { useActiveContextSync } from "@/hooks/useActiveContextSync";
import SolutionTabs from "@/components/pipeline/SolutionTabs";
import type { UIDesignRead } from "@/types/uiDesign";

// ─── UIDesignPage — Step 2B ───────────────────────────────────────────────────

type Device = "desktop" | "tablet" | "mobile";

const DEVICE_WIDTH: Record<Device, string> = {
  desktop: "100%",
  tablet: "768px",
  mobile: "390px",
};

export default function UIDesignPage() {
  const { slug, versionId } = useParams<{ slug: string; versionId: string }>();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [version, setVersion] = useState<Version | null>(null);
  const [uiDesign, setUIDesign] = useState<UIDesignRead | null>(null);
  const [profspecContent, setProfspecContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Chat
  const [chatHistory, setChatHistory] = useState<ChatHistoryItem[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatStreaming, setChatStreaming] = useState(false);
  const [chatBuffer, setChatBuffer] = useState("");
  const [htmlContent, setHtmlContent] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // Preview
  const [device, setDevice] = useState<Device>("desktop");

  // Approve
  const [approving, setApproving] = useState(false);

  // Init generation
  const [initializing, setInitializing] = useState(false);

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
        listUIDesigns({ project_id: proj.id, limit: 1 }),
        listProfessionalSpecs({ project_id: proj.id, limit: 1 }),
      ]).then(([uiRes, profRes]) => {
        if (cancelled) return;
        const ui = uiRes.items[0] ?? null;
        setUIDesign(ui);
        if (ui?.html_preview) setHtmlContent(ui.html_preview);
        setProfspecContent(profRes.items[0]?.content ?? "");
        setLoading(false);
      });
    }).catch(() => { if (!cancelled) { setError("Nepodarilo sa načítať dáta."); setLoading(false); } });
    return () => { cancelled = true; };
  }, [slug, versionId]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatHistory, chatBuffer]);

  async function handleInitialize() {
    if (!project || !version) return;
    setInitializing(true);
    try {
      const created = await createUIDesign({ project_id: project.id });
      setUIDesign(created);
      // Trigger initial generation
      let htmlAccum = "";
      let chatAccum = "";
      setChatStreaming(true);
      setChatBuffer("");
      abortRef.current = generateUIDesign(
        created.id,
        project.name,
        profspecContent,
        (event: UIDesignSSEEvent) => {
          if (event.type === "chat_chunk") {
            chatAccum += event.content;
            setChatBuffer(chatAccum);
          } else if (event.type === "html_chunk") {
            htmlAccum += event.content;
            setHtmlContent(htmlAccum);
          } else if (event.type === "done") {
            setChatStreaming(false);
            setChatHistory([{ role: "assistant", content: chatAccum || "Základný mockup vygenerovaný." }]);
            setChatBuffer("");
            // Save html_preview to backend
            if (created.id && htmlAccum) {
              updateUIDesign(created.id, { html_preview: htmlAccum }).then(setUIDesign).catch(() => {});
            }
          } else if (event.type === "error") {
            setChatStreaming(false);
            setChatBuffer("");
          }
        },
      );
    } finally {
      setInitializing(false);
    }
  }

  function handleSendMessage() {
    if (!uiDesign || !chatInput.trim() || chatStreaming) return;
    const msg = chatInput.trim();
    setChatInput("");
    setChatHistory((h) => [...h, { role: "user", content: msg }]);
    setChatStreaming(true);
    setChatBuffer("");

    let htmlAccum = "";
    let chatAccum = "";

    abortRef.current = chatUIDesign(
      uiDesign.id,
      msg,
      uiDesign.content,
      htmlContent,
      chatHistory,
      (event: UIDesignSSEEvent) => {
        if (event.type === "chat_chunk") {
          chatAccum += event.content;
          setChatBuffer(chatAccum);
        } else if (event.type === "html_chunk") {
          htmlAccum += event.content;
          setHtmlContent(htmlAccum);
        } else if (event.type === "done") {
          setChatStreaming(false);
          setChatHistory((h) => [...h, { role: "assistant", content: chatAccum }]);
          setChatBuffer("");
          // Persist html_preview
          if (htmlAccum) {
            updateUIDesign(uiDesign.id, { html_preview: htmlAccum })
              .then(setUIDesign)
              .catch(() => {});
          }
        } else if (event.type === "error") {
          setChatStreaming(false);
          setChatBuffer("");
        }
      },
    );
  }

  async function handleApprove() {
    if (!uiDesign || approving) return;
    setApproving(true);
    try {
      const updated = await updateUIDesign(uiDesign.id, {
        approved_by: user?.id ?? null,
        approved_at: new Date().toISOString(),
      });
      setUIDesign(updated);
    } finally {
      setApproving(false);
    }
  }

  if (loading) return <LoadingSpinner />;
  if (error || !project || !version) return <ErrorPanel msg={error} />;

  const isApproved = !!uiDesign?.approved_at;

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
        <span className="text-xs font-medium text-primary-400">Krok 2B/7 — UI Design</span>
        {isApproved && (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-500/10 border border-green-500/25 text-green-400">✓ Schválený</span>
        )}
        <div className="flex-1" />
        {uiDesign && !isApproved && (
          <button
            onClick={handleApprove}
            disabled={approving || !htmlContent}
            className="flex items-center gap-1.5 text-xs bg-green-600 hover:bg-green-500 disabled:opacity-40 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
            {approving ? "Schvaľujem…" : "Schváliť mockup"}
          </button>
        )}
        {isApproved && (
          <button
            onClick={() => navigate(`/projects/${slug}/versions/${versionId}/summary`)}
            className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
          >
            Krok 3 →
          </button>
        )}
      </div>
      <SolutionTabs slug={slug!} versionId={versionId!} />

      {/* Empty state — no UIDesign yet */}
      {!uiDesign && !initializing && (
        <div className="flex-1 flex flex-col items-center justify-center p-10 text-center">
          <div className="w-14 h-14 rounded-2xl bg-primary-600/20 border border-primary-500/30 flex items-center justify-center mx-auto mb-5">
            <svg className="w-7 h-7 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
          </div>
          <p className="text-sm font-semibold text-slate-200 mb-1">UI Design — Step 2B</p>
          <p className="text-xs text-slate-500 max-w-sm mb-5 leading-relaxed">
            AI vygeneruje základný HTML prototype aplikácie. Potom ho môžeš upravovať cez chat.
          </p>
          <button
            onClick={handleInitialize}
            className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-5 py-2 rounded-lg font-medium transition-colors"
          >
            Vygenerovať základný mockup
          </button>
        </div>
      )}

      {/* Generating initial */}
      {!uiDesign && initializing && (
        <div className="flex-1 flex flex-col items-center justify-center p-10 text-center gap-3">
          <svg className="w-6 h-6 animate-spin text-primary-400" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <p className="text-sm text-slate-400">Generujem základný prototype…</p>
        </div>
      )}

      {/* Split panel */}
      {uiDesign && (
        <div className="flex-1 overflow-hidden flex">
          {/* Left: AI Chat */}
          <div className="w-80 flex-shrink-0 flex flex-col border-r border-slate-800">
            <div className="px-4 py-2.5 border-b border-slate-800 flex-shrink-0">
              <div className="text-xs font-semibold text-slate-400">AI dizajnér</div>
              <div className="text-[10px] text-slate-600">Upravuj mockup cez chat</div>
            </div>
            <div className="flex-1 overflow-y-auto p-3 space-y-3">
              {chatHistory.length === 0 && !chatStreaming && (
                <div className="text-xs text-slate-700 text-center py-6">
                  Opíš čo chceš zmeniť v UI…
                </div>
              )}
              {chatHistory.map((msg, i) => (
                <div key={i} className={`flex gap-2 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                  {msg.role === "assistant" && (
                    <div className="w-6 h-6 rounded-full bg-primary-600/30 flex items-center justify-center text-[9px] text-primary-400 font-bold shrink-0 mt-0.5">AI</div>
                  )}
                  <div className={`max-w-[85%] text-xs px-3 py-2 rounded-lg leading-relaxed ${
                    msg.role === "user"
                      ? "bg-primary-600/20 text-slate-200 rounded-tr-none"
                      : "bg-slate-800 text-slate-300 rounded-tl-none"
                  }`}>
                    {msg.content}
                  </div>
                </div>
              ))}
              {chatStreaming && chatBuffer && (
                <div className="flex gap-2 justify-start">
                  <div className="w-6 h-6 rounded-full bg-primary-600/30 flex items-center justify-center text-[9px] text-primary-400 font-bold shrink-0 mt-0.5">AI</div>
                  <div className="max-w-[85%] text-xs px-3 py-2 rounded-lg bg-slate-800 text-slate-300 rounded-tl-none leading-relaxed">
                    {chatBuffer}<span className="inline-block w-0.5 h-3 bg-primary-400 animate-pulse ml-0.5 align-bottom" />
                  </div>
                </div>
              )}
              <div ref={chatEndRef} />
            </div>
            <div className="border-t border-slate-800 p-3 flex-shrink-0 space-y-2">
              <textarea
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSendMessage(); } }}
                placeholder="Opíš úpravu… (Enter = odoslať)"
                rows={2}
                disabled={isApproved}
                className="w-full text-xs bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 placeholder-slate-600 focus:outline-none focus:border-primary-500 resize-none disabled:opacity-40"
              />
              <div className="flex items-center gap-2">
                <button
                  onClick={handleSendMessage}
                  disabled={!chatInput.trim() || chatStreaming || isApproved}
                  className="flex-1 text-xs bg-primary-600 hover:bg-primary-500 disabled:opacity-40 text-white py-1.5 rounded-lg font-medium transition-colors"
                >
                  {chatStreaming ? "Pracujem…" : "Odoslať"}
                </button>
                {chatStreaming && (
                  <button
                    onClick={() => abortRef.current?.abort()}
                    className="text-xs text-slate-600 hover:text-red-400 transition-colors"
                  >
                    Stop
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Right: Preview */}
          <div className="flex-1 flex flex-col overflow-hidden bg-slate-950">
            {/* Preview toolbar */}
            <div className="flex-shrink-0 flex items-center gap-3 px-4 py-2 border-b border-slate-800 bg-slate-900/50">
              {/* Device toggle */}
              <div className="flex items-center gap-0.5 bg-slate-800 rounded-lg p-0.5">
                {(["desktop", "tablet", "mobile"] as Device[]).map((d) => (
                  <button
                    key={d}
                    onClick={() => setDevice(d)}
                    className={`px-2.5 py-1 rounded text-[11px] font-medium transition-colors ${
                      device === d ? "bg-slate-700 text-white" : "text-slate-500 hover:text-slate-300"
                    }`}
                  >
                    {d === "desktop" ? "Desktop" : d === "tablet" ? "Tablet" : "Mobile"}
                  </button>
                ))}
              </div>
              <div className="flex-1" />
              {chatStreaming && (
                <div className="flex items-center gap-1.5 text-[11px] text-primary-400">
                  <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Generujem…
                </div>
              )}
              <span className="text-[10px] text-slate-600 font-mono">{DEVICE_WIDTH[device]}</span>
            </div>

            {/* iframe wrapper */}
            <div className="flex-1 flex items-start justify-center p-4 overflow-auto">
              {htmlContent ? (
                <div
                  className="flex flex-col rounded-xl border border-slate-800 overflow-hidden shadow-2xl transition-all duration-300"
                  style={{ width: DEVICE_WIDTH[device], maxWidth: "100%", minHeight: "100%", height: "100%" }}
                >
                  {/* Fake browser bar */}
                  <div className="flex items-center gap-2 px-4 py-2 bg-slate-800 border-b border-slate-700 flex-shrink-0">
                    <div className="flex gap-1.5">
                      <div className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
                      <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/60" />
                      <div className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
                    </div>
                    <div className="flex-1 bg-slate-700 rounded px-3 py-0.5 text-[11px] text-slate-500 font-mono">
                      localhost:{project.slug}
                    </div>
                  </div>
                  <iframe
                    srcDoc={htmlContent}
                    className="flex-1 w-full border-0"
                    sandbox="allow-scripts"
                    title="UI Design Preview"
                  />
                </div>
              ) : (
                <div className="flex items-center justify-center h-full text-slate-700 text-sm">
                  {chatStreaming ? "Generujem HTML preview…" : "Žiadny mockup"}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
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

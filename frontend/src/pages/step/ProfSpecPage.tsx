import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import { getVersion } from "@/services/api/versions";
import { listProfessionalSpecs, chatProfessionalSpec, updateProfessionalSpec } from "@/services/api/professionalSpecifications";
import { useAuthStore } from "@/store/authStore";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";
import { useActiveContextSync } from "@/hooks/useActiveContextSync";
import SolutionTabs from "@/components/pipeline/SolutionTabs";
import SlovakTextarea from "@/components/editor/SlovakTextarea";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ProfessionalSpecificationRead } from "@/types/professionalSpecification";
import type { SpecChatHistoryItem } from "@/services/api/professionalSpecifications";

// ─── ProfSpecPage — Step 2 ────────────────────────────────────────────────────

export default function ProfSpecPage() {
  const { slug, versionId } = useParams<{ slug: string; versionId: string }>();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [version, setVersion] = useState<Version | null>(null);
  const [spec, setSpec] = useState<ProfessionalSpecificationRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Chat
  const [chatHistory, setChatHistory] = useState<SpecChatHistoryItem[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatStreaming, setChatStreaming] = useState(false);
  const [chatBuffer, setChatBuffer] = useState("");
  const [specContent, setSpecContent] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // Approve
  const [approving, setApproving] = useState(false);

  // Manual edit of the spec content (overrides AI-generated output).
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);

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
      return listProfessionalSpecs({ project_id: proj.id, limit: 1 }).then((res) => {
        if (cancelled) return;
        const s = res.items[0] ?? null;
        setSpec(s);
        if (s) setSpecContent(s.content);
        setLoading(false);
      });
    }).catch(() => { if (!cancelled) { setError("Nepodarilo sa načítať dáta."); setLoading(false); } });
    return () => { cancelled = true; };
  }, [slug, versionId]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatHistory, chatBuffer]);

  function handleSendMessage() {
    if (!spec || !chatInput.trim() || chatStreaming) return;
    const msg = chatInput.trim();
    setChatInput("");
    setChatHistory((h) => [...h, { role: "user", content: msg }]);
    setChatStreaming(true);
    setChatBuffer("");

    abortRef.current = chatProfessionalSpec(
      spec.id,
      msg,
      specContent,
      chatHistory,
      (chunk) => setChatBuffer((prev) => prev + chunk),
      (chunk) => setSpecContent((prev) => prev + chunk),
      () => {
        setChatStreaming(false);
        setChatHistory((h) => {
          const lastBuf = chatBuffer || "";
          return [...h, { role: "assistant", content: lastBuf }];
        });
        setChatBuffer("");
      },
      (err) => {
        setChatStreaming(false);
        setChatBuffer("");
        console.error("Chat error:", err);
      },
    );
  }

  async function handleApprove() {
    if (!spec || approving) return;
    setApproving(true);
    try {
      const updated = await updateProfessionalSpec(spec.id, {
        approved_by: user?.id ?? null,
        approved_at: new Date().toISOString(),
      });
      setSpec(updated);
    } finally {
      setApproving(false);
    }
  }

  function handleStartEdit() {
    setEditContent(specContent);
    setEditing(true);
  }

  function handleCancelEdit() {
    setEditing(false);
  }

  async function handleSaveEdit() {
    if (!spec || saving || !editContent.trim()) return;
    setSaving(true);
    try {
      const updated = await updateProfessionalSpec(spec.id, { content: editContent });
      setSpec(updated);
      setSpecContent(updated.content);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  async function handleCopy() {
    const text = editing ? editContent : specContent;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API can fail in insecure contexts — silent no-op.
    }
  }

  if (loading) return <LoadingSpinner />;
  if (error || !project || !version) return <ErrorPanel msg={error} />;

  // Gate: no professional spec yet
  if (!spec) {
    return (
      <div className="flex flex-col h-full">
        <StepHeader project={project} version={version} slug={slug!} versionId={versionId!} stepN={2} stepLabel="Vývojová dokumentácia" />
        <SolutionTabs slug={slug!} versionId={versionId!} />
        <div className="flex-1 flex flex-col items-center justify-center p-10 text-center">
          <svg className="w-12 h-12 text-slate-700 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
          </svg>
          <p className="text-sm text-slate-500 mb-1">Čaká na Krok 1</p>
          <p className="text-xs text-slate-700 mb-4">Vývojová dokumentácia sa vygeneruje po dokončení zákazníckej špecifikácie.</p>
          <button
            onClick={() => navigate(`/projects/${slug}/versions/${versionId}/spec`)}
            className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
          >
            ← Krok 1 — Zákaznícka špecifikácia
          </button>
        </div>
      </div>
    );
  }

  const isApproved = !!spec.approved_at;

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
        <span className="text-xs font-medium text-primary-400">Krok 2/7 — Vývojová dokumentácia</span>
        {isApproved && (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-500/10 border border-green-500/25 text-green-400">
            ✓ Schválená
          </span>
        )}
        <div className="flex-1" />
        {!isApproved && (
          <button
            onClick={handleApprove}
            disabled={approving}
            className="text-xs bg-green-600 hover:bg-green-500 disabled:opacity-40 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
          >
            {approving ? "Schvaľujem…" : "Schváliť →"}
          </button>
        )}
        {isApproved && (
          <button
            onClick={() => navigate(`/projects/${slug}/versions/${versionId}/architecture`)}
            className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
          >
            Krok 4 →
          </button>
        )}
      </div>
      <SolutionTabs slug={slug!} versionId={versionId!} />

      {/* Split panel */}
      <div className="flex-1 overflow-hidden flex">
        {/* Left: AI Chat */}
        <div className="w-80 flex-shrink-0 flex flex-col border-r border-slate-800">
          <div className="px-4 py-2.5 border-b border-slate-800 flex-shrink-0">
            <div className="text-xs font-semibold text-slate-400">AI konzultant</div>
            <div className="text-[10px] text-slate-600">Upresni dokumentáciu cez chat</div>
          </div>
          <div className="flex-1 overflow-y-auto p-3 space-y-3">
            {chatHistory.length === 0 && !chatStreaming && (
              <div className="text-xs text-slate-700 text-center py-6">
                Použi chat pre upresnenie alebo rozšírenie špecifikácie.
              </div>
            )}
            {chatHistory.map((msg, i) => (
              <div key={i} className={`flex gap-2 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                {msg.role === "assistant" && (
                  <div className="w-6 h-6 rounded-full bg-primary-600/30 flex items-center justify-center text-[9px] text-primary-400 font-bold shrink-0 mt-0.5">AI</div>
                )}
                <div className={`max-w-[85%] text-xs px-3 py-2 rounded-lg ${
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
                <div className="max-w-[85%] text-xs px-3 py-2 rounded-lg bg-slate-800 text-slate-300 rounded-tl-none">
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
              placeholder="Napíš správu… (Enter = odoslať)"
              rows={2}
              className="w-full text-xs bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 placeholder-slate-600 focus:outline-none focus:border-primary-500 resize-none"
            />
            <button
              onClick={handleSendMessage}
              disabled={!chatInput.trim() || chatStreaming}
              className="w-full text-xs bg-primary-600 hover:bg-primary-500 disabled:opacity-40 text-white py-1.5 rounded-lg font-medium transition-colors"
            >
              {chatStreaming ? "Odpovedám…" : "Odoslať"}
            </button>
          </div>
        </div>

        {/* Right: Spec content */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="px-4 py-2 border-b border-slate-800 flex-shrink-0 flex items-center gap-2">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-widest">Vývojová dokumentácia</span>
            <span className="text-[10px] text-slate-600 font-mono">v{spec.version}</span>
            <div className="flex-1" />
            <button
              onClick={handleCopy}
              disabled={!(editing ? editContent : specContent).trim()}
              className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 border border-slate-700 hover:border-slate-500 disabled:opacity-40 px-2 py-1 rounded transition-colors"
            >
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
              {copied ? "Skopírované ✓" : "Kopírovať"}
            </button>
            {!editing && (
              <button
                onClick={handleStartEdit}
                className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 border border-slate-700 hover:border-slate-500 px-2 py-1 rounded transition-colors"
              >
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                </svg>
                Upraviť
              </button>
            )}
            {editing && (
              <>
                <button
                  onClick={handleCancelEdit}
                  className="text-xs text-slate-500 border border-slate-700 px-2 py-1 rounded hover:border-slate-500 transition-colors"
                >
                  Zrušiť
                </button>
                <button
                  onClick={handleSaveEdit}
                  disabled={saving || !editContent.trim()}
                  className="text-xs bg-primary-600 hover:bg-primary-500 disabled:opacity-40 text-white px-3 py-1 rounded font-medium transition-colors"
                >
                  {saving ? "Ukladám…" : "Uložiť"}
                </button>
              </>
            )}
          </div>
          {editing ? (
            <SlovakTextarea
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              autoFocus
              className="flex-1 w-full bg-slate-950"
            />
          ) : (
            <div className="flex-1 overflow-y-auto p-5">
              <div
                className="prose prose-invert prose-sm max-w-none
                  prose-headings:text-slate-100 prose-headings:font-semibold
                  prose-h1:text-2xl prose-h2:text-lg prose-h3:text-base prose-h4:text-sm
                  prose-p:text-slate-300 prose-li:text-slate-300
                  prose-strong:text-slate-100
                  prose-code:text-primary-300 prose-code:bg-slate-800 prose-code:px-1 prose-code:rounded
                  prose-code:before:content-none prose-code:after:content-none
                  prose-table:text-sm prose-th:text-slate-200 prose-td:text-slate-300
                  prose-th:border-slate-700 prose-td:border-slate-800
                  prose-hr:border-slate-800
                  prose-a:text-primary-400 hover:prose-a:text-primary-300"
              >
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{specContent}</ReactMarkdown>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Shared helpers ───────────────────────────────────────────────────────────

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

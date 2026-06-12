/**
 * Project Specs — filesystem browser for /opt/projects/{slug}/docs/.
 *
 * Top-level page that complements ``/kb`` (Knowledge Base, ICC-wide
 * documents). While KB renders documents under ``/home/icc/knowledge``,
 * this page renders the agent-produced specs that live in each
 * project's own repo. Tree is keyed by project slug at the top level.
 *
 * Permissions (v1):
 * - List + content read: ``ri`` only (Director).
 * - Edit: ``ri`` only. New documents are produced by the three agents
 *   (Designer / Implementer / Auditor) directly in the project repo,
 *   not via this UI.
 *
 * Reuses :file:`src/components/KbTree.tsx` and
 * :file:`src/lib/kbTreeBuilder.ts` — built in 2026-05-13 for the KB
 * refactor, now paying off in shared infra.
 */

import { useState, useEffect, useCallback } from "react";
import {
  FolderOpen,
  RefreshCw,
  Database,
  Pencil,
  Save,
  X,
  Eye,
  Loader2,
  Lock,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { useAuthStore } from "@/store/authStore";
import { KbTree } from "@/components/KbTree";
import { CodeBlock } from "@/components/markdown/CodeBlock";
import type { KnowledgeDoc } from "@/types/knowledge";
import { ApiError } from "@/services/api";
import {
  listProjectSpecs,
  getProjectSpecContent,
  updateProjectSpecContent,
  splitProjectPath,
} from "@/services/api/projectSpecs";

type ViewMode = "tree" | "all";

export default function ProjectSpecsPage() {
  const user = useAuthStore((s) => s.user);
  const isDirector = user?.role === "ri";

  // List state
  const [documents, setDocuments] = useState<KnowledgeDoc[]>([]);
  const [loading, setLoading] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("tree");

  // Viewer state
  const [selectedDoc, setSelectedDoc] = useState<KnowledgeDoc | null>(null);
  const [docContent, setDocContent] = useState("");
  /** False when the backend reported a binary file — frontend shows a
   *  "cannot display" placeholder instead of trying to render bytes. */
  const [docIsText, setDocIsText] = useState(true);
  const [loadingContent, setLoadingContent] = useState(false);

  // Edit state
  const [editMode, setEditMode] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);

  const [error, setError] = useState("");

  // --- Loaders ---

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const docs = await listProjectSpecs();
      setDocuments(docs);
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.message
          : "Chyba pri načítaní zoznamu dokumentov",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDocContent = useCallback(async (doc: KnowledgeDoc) => {
    // Tree builder may pass a synthetic directory node (empty folder
    // entry) — there is no content to load for those.
    if (doc.is_directory) {
      setSelectedDoc(doc);
      setEditMode(false);
      setDocContent("");
      setDocIsText(true);
      setError("");
      return;
    }
    const parts = splitProjectPath(doc.relative_path);
    if (!parts) {
      setError("Neplatná cesta dokumentu");
      return;
    }
    setSelectedDoc(doc);
    setEditMode(false);
    setLoadingContent(true);
    setError("");
    try {
      const resp = await getProjectSpecContent(parts.slug, parts.path);
      setDocContent(resp.content);
      setDocIsText(resp.is_text);
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.message
          : "Chyba pri načítaní obsahu dokumentu",
      );
    } finally {
      setLoadingContent(false);
    }
  }, []);

  const saveEdit = useCallback(async () => {
    if (!selectedDoc) return;
    const parts = splitProjectPath(selectedDoc.relative_path);
    if (!parts) return;
    setSaving(true);
    setError("");
    try {
      await updateProjectSpecContent(parts.slug, parts.path, editContent);
      setDocContent(editContent);
      setEditMode(false);
      // size_bytes is stale — refresh list in the background
      refresh();
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : "Chyba pri ukladaní dokumentu",
      );
    } finally {
      setSaving(false);
    }
  }, [selectedDoc, editContent, refresh]);

  // --- Initial load ---

  useEffect(() => {
    refresh();
  }, [refresh]);

  // --- Render ---

  if (!isDirector) {
    // Non-ri users see a clear gate instead of an empty list.
    return (
      <div className="flex h-[calc(100vh-100px)] items-center justify-center bg-gray-900 rounded-xl border border-gray-700">
        <div className="flex flex-col items-center gap-3 text-gray-400">
          <Lock size={48} />
          <p className="text-sm">
            Špecifikácie sú dostupné iba Directorovi (ri rola).
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100vh-100px)] bg-gray-900 rounded-xl border border-gray-700 overflow-hidden">
      {/* Tree column — identický pattern s /kb stránkou */}
      <div className="w-80 bg-gray-800 border-r border-gray-700 flex flex-col min-w-0">
        {/* Header */}
        <div className="p-3 border-b border-gray-700">
          <h2 className="text-base font-semibold flex items-center gap-2 text-gray-100">
            <FolderOpen size={18} />
            Špecifikácie
          </h2>
          <div className="mt-2 flex items-center gap-1.5 text-xs text-gray-400">
            <Database size={12} />
            <span>{documents.length} dokumentov</span>
          </div>
        </div>

        {/* Toolbar */}
        <div className="p-3 border-b border-gray-700 flex gap-1.5">
          <button
            onClick={refresh}
            className="p-1.5 bg-gray-700 text-gray-300 rounded hover:bg-gray-600 transition-colors"
            title="Obnoviť"
          >
            <RefreshCw size={14} />
          </button>
          <button
            onClick={() => setViewMode((m) => (m === "tree" ? "all" : "tree"))}
            className={`flex-1 px-2 py-1.5 rounded text-xs font-medium transition-colors ${
              viewMode === "all"
                ? "bg-blue-600 text-white hover:bg-blue-500"
                : "bg-gray-700 text-gray-300 hover:bg-gray-600"
            }`}
            title="Prepínať medzi hierarchickým stromom a plochým zoznamom"
          >
            {viewMode === "tree" ? "Všetky dokumenty" : "← Strom"}
          </button>
        </div>

        {error && (
          <div className="px-3 py-2 bg-red-900/30 border-b border-red-800/50 text-red-400 text-xs flex items-center justify-between">
            <span className="truncate">{error}</span>
            <button
              onClick={() => setError("")}
              className="text-red-400 hover:text-red-300 ml-2"
            >
              <X size={12} />
            </button>
          </div>
        )}

        {/* Tree / flat list */}
        <div className="flex-1 overflow-y-auto py-1">
          {loading ? (
            <div className="p-4 flex items-center gap-2 text-gray-400 text-sm">
              <Loader2 size={14} className="animate-spin" /> Načítavam...
            </div>
          ) : documents.length === 0 ? (
            <div className="p-4 text-gray-500 text-xs">Žiadne dokumenty</div>
          ) : viewMode === "all" ? (
            documents.map((doc) => (
              <button
                key={doc.relative_path}
                onClick={() => loadDocContent(doc)}
                className={`w-full text-left p-2 border-b border-gray-700/50 hover:bg-gray-700/50 transition-colors ${
                  selectedDoc?.relative_path === doc.relative_path
                    ? "bg-gray-700"
                    : ""
                }`}
              >
                <div className="flex items-center gap-2">
                  <span aria-hidden="true">📄</span>
                  <span className="font-medium truncate text-xs text-gray-100">
                    {doc.filename}
                  </span>
                </div>
                <div className="text-[10px] text-gray-500 mt-0.5">
                  {doc.category} · {(doc.size_bytes / 1024).toFixed(1)} kB
                </div>
              </button>
            ))
          ) : (
            <KbTree
              documents={documents}
              selectedPath={selectedDoc?.relative_path ?? null}
              onSelect={loadDocContent}
            />
          )}
        </div>
      </div>

      {/* Viewer / Editor */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {selectedDoc ? (
          editMode ? (
            <div className="flex-1 flex flex-col p-4 overflow-hidden">
              <div className="flex items-center justify-between mb-3">
                <div className="text-sm text-gray-300 truncate">
                  Upraviť: <span className="text-gray-100">{selectedDoc.relative_path}</span>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={saveEdit}
                    disabled={saving}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white rounded hover:bg-blue-500 text-sm font-medium disabled:opacity-50"
                  >
                    {saving ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Save size={14} />
                    )}
                    Uložiť
                  </button>
                  <button
                    onClick={() => setEditMode(false)}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-700 text-gray-300 rounded hover:bg-gray-600 text-sm"
                  >
                    <X size={14} /> Zrušiť
                  </button>
                </div>
              </div>
              <textarea
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                className="flex-1 p-3 bg-gray-900 border border-gray-700 rounded text-sm text-gray-100 font-mono resize-none focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
          ) : (
            <div className="flex-1 flex flex-col overflow-hidden">
              <div className="flex items-center justify-between p-3 border-b border-gray-700">
                <div className="text-sm text-gray-300 truncate">
                  <span className="text-gray-500">
                    {selectedDoc.is_directory ? "📂" : "📄"}
                  </span>{" "}
                  <span className="text-gray-100">{selectedDoc.relative_path}</span>
                </div>
                {/* Edit button — Markdown files only. Non-.md files
                    (CSV, JSON, etc.) are read-only per service contract;
                    binary files (is_text=false) and directory entries
                    have no content to edit. */}
                {selectedDoc.filename.toLowerCase().endsWith(".md") &&
                  !selectedDoc.is_directory &&
                  docIsText && (
                    <button
                      onClick={() => {
                        setEditContent(docContent);
                        setEditMode(true);
                      }}
                      className="flex items-center gap-1.5 px-2 py-1 bg-gray-700 text-gray-300 rounded hover:bg-gray-600 text-xs"
                    >
                      <Pencil size={12} /> Upraviť
                    </button>
                  )}
              </div>
              <div className="flex-1 overflow-y-auto p-6">
                {loadingContent ? (
                  <div className="flex items-center gap-2 text-gray-400">
                    <Loader2 size={16} className="animate-spin" /> Načítavam obsah...
                  </div>
                ) : selectedDoc.is_directory ? (
                  // Empty directory placeholder — folder exists on disk
                  // but has no contents yet.
                  <div className="text-sm text-gray-500 italic">
                    Prázdny adresár ({selectedDoc.relative_path}). Pridaj
                    sem súbory cez SSH alebo cez agentov; po obnove sa
                    objavia v strome.
                  </div>
                ) : !docIsText ? (
                  // Binary file — backend returned is_text=false. No
                  // content payload to render; the user can SSH to view.
                  <div className="text-sm text-gray-400">
                    <p className="mb-2">
                      <span className="text-gray-500">⚠️</span> Binárny
                      súbor — obsah sa nedá zobraziť v prehliadači.
                    </p>
                    <p className="text-xs text-gray-500">
                      Veľkosť: {(selectedDoc.size_bytes / 1024).toFixed(1)} kB.
                      Pre prezeranie použi SSH alebo lokálny prehliadač.
                    </p>
                  </div>
                ) : selectedDoc.filename.toLowerCase().endsWith(".md") ? (
                  <div className="prose prose-invert prose-sm max-w-none">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        code({ className, children, ...props }) {
                          const match = /language-(\w+)/.exec(className || "");
                          const isInline =
                            !className &&
                            typeof children === "string" &&
                            !children.includes("\n");
                          if (!isInline && match) {
                            return (
                              <CodeBlock language={match[1]}>
                                {String(children)}
                              </CodeBlock>
                            );
                          }
                          if (
                            !isInline &&
                            typeof children === "string" &&
                            children.includes("\n")
                          ) {
                            return <CodeBlock>{String(children)}</CodeBlock>;
                          }
                          return (
                            <code
                              className="bg-gray-800 px-1.5 py-0.5 rounded text-sm"
                              {...props}
                            >
                              {children}
                            </code>
                          );
                        },
                        pre({ children }) {
                          return <>{children}</>;
                        },
                      }}
                    >
                      {docContent}
                    </ReactMarkdown>
                  </div>
                ) : (
                  // Plain text file (CSV / JSON / YAML / source code /
                  // shell etc.) — render verbatim in monospace.
                  <pre className="text-xs text-gray-200 font-mono whitespace-pre-wrap break-all">
                    {docContent}
                  </pre>
                )}
              </div>
            </div>
          )
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-gray-500">
            <Eye size={48} className="mb-4 text-gray-600" />
            <p className="text-sm">Vyber dokument zo stromu</p>
          </div>
        )}
      </div>
    </div>
  );
}

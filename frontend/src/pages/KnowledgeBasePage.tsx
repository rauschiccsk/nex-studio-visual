/**
 * Knowledge Base — filesystem browser + viewer + editor + creator.
 *
 * Ported 1:1 from NEX Command `frontend/src/components/rag/KnowledgeBrowser.tsx`
 * (747 lines) per Director mandate 2026-05-07 (M1.D milestone).
 * Page wraps the browser at route ``/kb`` (see App.tsx).
 *
 * Adaptations for NEX Studio:
 *
 * * API style — NEX Studio's ``api`` wrapper returns ``T`` directly
 *   (NEX Command returns ``{ data: T }`` axios-like envelope).
 * * API prefix — ``/api/v1/knowledge/*`` (NEX Studio convention) vs
 *   NEX Command ``/api/knowledge/*``.
 * * AuthUser shape — NEX Studio has ``role: 'ri' | 'ha' | 'shu'``;
 *   NEX Command had ``role`` + ``shuhari_phase``. Mapping:
 *     ``isDirector`` (NEX Command "director" role) → ``role === 'ri'``
 *     ``canWrite`` (NEX Command ``shuhari_phase !== 'shu'``) → ``role !== 'shu'``
 * * RAG endpoints (``/api/rag/stats``, ``/api/rag/search``, ``/api/rag/document``)
 *   are stubbed in M1 — wired up in M3 (RAG search milestone).
 *   The "Hľadať" button shows an info banner; vector search returns no results.
 * * AuditDashboard "Quality" sub-tab is dropped in M1 — comes back in
 *   M8 (Audit/Reports milestone).
 */

import { useState, useEffect, useCallback, useRef } from "react";
import {
  Search,
  FileText,
  FolderOpen,
  RefreshCw,
  Database,
  Plus,
  Pencil,
  Trash2,
  Save,
  X,
  Eye,
  Loader2,
  Copy,
  Check,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, ApiError } from "@/services/api";
import { useAuthStore } from "@/store/authStore";
import { useSessionStore } from "@/store/sessionStore";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";
import { CodeBlock } from "@/components/markdown/CodeBlock";

// --- Interfaces ---

interface KnowledgeDoc {
  relative_path: string;
  filename: string;
  category: string;
  size_bytes: number;
}

// --- Helpers ---

function makeTitle(filename: string): string {
  let name = filename;
  if (name.endsWith(".md")) name = name.slice(0, -3);
  return name.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function titleToFilename(title: string): string {
  return (
    title
      .trim()
      .toUpperCase()
      .replace(/\s+/g, "_")
      .replace(/[^A-Z0-9_]/g, "") + ".md"
  );
}

function extractProjectFromPath(relativePath: string): string | null {
  // Pre "Všetky" view: extract <slug> z "projects/<slug>/STATUS.md".
  const parts = relativePath.split("/");
  if (parts.length >= 2 && parts[0] === "projects" && parts[1]) {
    return parts[1];
  }
  return null;
}

// --- Component ---

export default function KnowledgeBasePage() {
  const user = useAuthStore((s) => s.user);
  const isDirector = user?.role === "ri";
  const canWrite = user?.role !== "shu";

  const sessionRestored = useRef(false);

  // List state
  const [documents, setDocuments] = useState<KnowledgeDoc[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(
    () => useSessionStore.getState().knowledgeCategory,
  );
  const [loading, setLoading] = useState(false);

  // Viewer state
  const [selectedDoc, setSelectedDoc] = useState<KnowledgeDoc | null>(null);
  const [docContent, setDocContent] = useState("");
  const [loadingContent, setLoadingContent] = useState(false);

  // Mode: 'browse' | 'edit' | 'create'
  const [mode, setMode] = useState<"browse" | "edit" | "create">("browse");

  // Edit state
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);

  // Create state
  const [newTitle, setNewTitle] = useState("");
  const [newCategory, setNewCategory] = useState("icc");
  const [newFilename, setNewFilename] = useState("");
  const [newContent, setNewContent] = useState("");

  // Delete state
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Search state — RAG stubbed in M1
  const [searchQuery, setSearchQuery] = useState("");
  const [searchInfo, setSearchInfo] = useState<string | null>(null);

  // Copy
  const [copyDoc, isDocCopied] = useCopyToClipboard();

  // Error
  const [error, setError] = useState("");

  // --- API calls ---

  const loadCategories = useCallback(async () => {
    try {
      const data = await api.get<{ categories: string[] }>("/knowledge/categories");
      setCategories(data.categories);
    } catch {
      /* silent */
    }
  }, []);

  const loadDocuments = useCallback(async (category?: string | null) => {
    setLoading(true);
    setError("");
    try {
      const params = category ? { category } : {};
      const data = await api.get<{ documents: KnowledgeDoc[]; count: number }>(
        "/knowledge/documents",
        { params },
      );
      setDocuments(data.documents);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Chyba pri načítaní dokumentov");
    }
    setLoading(false);
  }, []);

  const loadDocContent = async (doc: KnowledgeDoc) => {
    setLoadingContent(true);
    setError("");
    try {
      const data = await api.get<{ relative_path: string; content: string }>(
        "/knowledge/documents/content",
        { params: { relative_path: doc.relative_path } },
      );
      setDocContent(data.content);
      setSelectedDoc(doc);
      setMode("browse");
      setSearchInfo(null);
      useSessionStore.getState().setKnowledgeDocPath(doc.relative_path);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Chyba pri načítaní dokumentu");
    }
    setLoadingContent(false);
  };

  const doSearch = () => {
    // M1 stub: vector search není wired up. Plně v M3 (RAG milestone).
    if (!searchQuery.trim()) {
      setSearchInfo(null);
      return;
    }
    setSearchInfo("Vector search bude k dispozícii v M3 (RAG milestone). Pre teraz použite filter podľa kategórie.");
  };

  const refresh = useCallback(async () => {
    await Promise.all([loadDocuments(selectedCategory), loadCategories()]);
  }, [loadDocuments, loadCategories, selectedCategory]);

  const handleCreate = async () => {
    if (!newTitle.trim() || !newCategory || !newContent.trim()) return;
    setSaving(true);
    setError("");
    const filename = newFilename.trim() || titleToFilename(newTitle);
    try {
      await api.post("/knowledge/documents", {
        category: newCategory,
        filename,
        content: newContent,
      });
      setMode("browse");
      setNewTitle("");
      setNewCategory("icc");
      setNewFilename("");
      setNewContent("");
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Chyba pri vytváraní dokumentu");
    }
    setSaving(false);
  };

  const handleUpdate = async () => {
    if (!selectedDoc || !editContent.trim()) return;
    setSaving(true);
    setError("");
    try {
      await api.put("/knowledge/documents", {
        relative_path: selectedDoc.relative_path,
        content: editContent,
      });
      setDocContent(editContent);
      setMode("browse");
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Chyba pri ukladaní");
    }
    setSaving(false);
  };

  const handleDelete = async () => {
    if (!selectedDoc) return;
    setDeleting(true);
    setError("");
    try {
      await api.delete("/knowledge/documents", {
        params: { relative_path: selectedDoc.relative_path },
      });
      setSelectedDoc(null);
      setDocContent("");
      setConfirmDelete(false);
      setMode("browse");
      useSessionStore.getState().setKnowledgeDocPath(null);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Chyba pri mazaní");
    }
    setDeleting(false);
  };

  // --- Effects ---

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadDocuments(selectedCategory);
    if (sessionRestored.current) {
      setSelectedDoc(null);
      setDocContent("");
    }
    setMode("browse");
    setSearchInfo(null);
    setSearchQuery("");
  }, [selectedCategory, loadDocuments]);

  useEffect(() => {
    if (sessionRestored.current || documents.length === 0) return;
    sessionRestored.current = true;
    const savedDocPath = useSessionStore.getState().knowledgeDocPath;
    if (savedDocPath) {
      const doc = documents.find((d) => d.relative_path === savedDocPath);
      if (doc) {
        loadDocContent(doc);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documents]);

  useEffect(() => {
    if (newTitle.trim()) {
      setNewFilename(titleToFilename(newTitle));
    }
  }, [newTitle]);

  // --- Render ---

  return (
    <div className="flex h-[calc(100vh-100px)] bg-gray-900 rounded-xl border border-gray-700 overflow-hidden">
      {/* Sidebar */}
      <div className="w-56 bg-gray-800 p-4 border-r border-gray-700 flex flex-col">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2 text-gray-100">
          <FolderOpen size={20} />
          Knowledge Base
        </h2>

        <div className="mb-4 p-3 bg-gray-700 rounded-lg text-sm">
          <div className="flex items-center gap-1.5 mb-1">
            <Database size={14} className="text-gray-400" />
            <span className="font-medium text-gray-100">{documents.length} dokumentov</span>
          </div>
        </div>

        <div className="space-y-1 flex-1 overflow-y-auto">
          <button
            onClick={() => {
              setSelectedCategory(null);
              useSessionStore.getState().setKnowledgeCategory(null);
              useSessionStore.getState().setKnowledgeDocPath(null);
            }}
            className={`w-full text-left px-3 py-2 rounded text-sm transition-colors ${
              !selectedCategory ? "bg-blue-600 text-white" : "text-gray-300 hover:bg-gray-700"
            }`}
          >
            Všetky dokumenty
          </button>
          {categories
            .filter((c) => isDirector || c !== "credentials")
            .map((cat) => (
              <button
                key={cat}
                onClick={() => {
                  setSelectedCategory(cat);
                  useSessionStore.getState().setKnowledgeCategory(cat);
                  useSessionStore.getState().setKnowledgeDocPath(null);
                }}
                className={`w-full text-left px-3 py-2 rounded text-sm transition-colors ${
                  selectedCategory === cat
                    ? "bg-blue-600 text-white"
                    : "text-gray-300 hover:bg-gray-700"
                }`}
              >
                {cat}
              </button>
            ))}
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Toolbar */}
        <div className="p-3 border-b border-gray-700 flex gap-2">
          <div className="flex-1 relative">
            <Search
              className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
              size={16}
            />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && doSearch()}
              placeholder="Vector search v knowledge base (M3)..."
              className="w-full pl-9 pr-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 placeholder:text-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <button
            onClick={doSearch}
            className="px-3 py-2 bg-gray-700 text-gray-300 rounded-lg hover:bg-gray-600 text-sm transition-colors"
          >
            Hľadať
          </button>
          <button
            onClick={() => {
              setSearchQuery("");
              setSearchInfo(null);
              refresh();
            }}
            className="p-2 bg-gray-700 text-gray-300 rounded-lg hover:bg-gray-600 transition-colors"
            title="Obnoviť"
          >
            <RefreshCw size={16} />
          </button>
          {canWrite && (
            <button
              onClick={() => {
                setMode("create");
                setSelectedDoc(null);
                setDocContent("");
                setNewTitle("");
                setNewCategory(selectedCategory || "icc");
                setNewFilename("");
                setNewContent("");
                setError("");
              }}
              className="flex items-center gap-1.5 px-3 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-500 text-sm font-medium transition-colors"
            >
              <Plus size={16} /> Nový
            </button>
          )}
        </div>

        {searchInfo && (
          <div className="px-4 py-2 bg-blue-900/30 border-b border-blue-800/50 text-blue-300 text-sm flex items-center justify-between">
            <span>{searchInfo}</span>
            <button onClick={() => setSearchInfo(null)} className="text-blue-300 hover:text-blue-200">
              <X size={14} />
            </button>
          </div>
        )}

        {error && (
          <div className="px-4 py-2 bg-red-900/30 border-b border-red-800/50 text-red-400 text-sm flex items-center justify-between">
            <span>{error}</span>
            <button onClick={() => setError("")} className="text-red-400 hover:text-red-300">
              <X size={14} />
            </button>
          </div>
        )}

        {/* Content area */}
        <div className="flex-1 flex overflow-hidden">
          {/* Document list */}
          <div className="w-72 border-r border-gray-700 flex flex-col">
            <div className="flex-1 overflow-y-auto">
              {loading ? (
                <div className="p-4 flex items-center gap-2 text-gray-400">
                  <Loader2 size={16} className="animate-spin" /> Načítavam...
                </div>
              ) : documents.length === 0 ? (
                <div className="p-4 text-gray-500 text-sm">Žiadne dokumenty</div>
              ) : (
                documents.map((doc) => {
                  const project = extractProjectFromPath(doc.relative_path);
                  return (
                    <button
                      key={doc.relative_path}
                      onClick={() => loadDocContent(doc)}
                      className={`w-full text-left p-3 border-b border-gray-700 hover:bg-gray-800 transition-colors ${
                        selectedDoc?.relative_path === doc.relative_path ? "bg-gray-800" : ""
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <FileText size={14} className="text-gray-400 flex-shrink-0" />
                        <span className="font-medium truncate text-sm text-gray-100">
                          {makeTitle(doc.filename)}
                        </span>
                      </div>
                      <div className="text-xs text-gray-500 mt-1">
                        {project && !selectedCategory && (
                          <span className="text-blue-400 mr-1.5">[{project}]</span>
                        )}
                        {doc.category} · {(doc.size_bytes / 1024).toFixed(1)} kB
                      </div>
                    </button>
                  );
                })
              )}
            </div>
            <div className="px-3 py-2 border-t border-gray-700 text-xs text-gray-500">
              {documents.length} dokumentov
            </div>
          </div>

          {/* Viewer / Editor / Creator */}
          <div className="flex-1 flex flex-col overflow-hidden">
            {mode === "create" ? (
              <div className="flex-1 flex flex-col p-4 overflow-y-auto">
                <h2 className="text-lg font-semibold text-gray-100 mb-4">Nový dokument</h2>
                <div className="space-y-3 mb-4">
                  <div>
                    <label className="block text-xs font-medium text-gray-400 mb-1">
                      Názov dokumentu
                    </label>
                    <input
                      type="text"
                      value={newTitle}
                      onChange={(e) => setNewTitle(e.target.value)}
                      placeholder="Popisný názov..."
                      className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                  <div className="flex gap-3">
                    <div className="flex-1">
                      <label className="block text-xs font-medium text-gray-400 mb-1">
                        Kategória
                      </label>
                      <select
                        value={newCategory}
                        onChange={(e) => setNewCategory(e.target.value)}
                        className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
                      >
                        {categories.map((c) => (
                          <option key={c} value={c}>
                            {c}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="flex-1">
                      <label className="block text-xs font-medium text-gray-400 mb-1">
                        Názov súboru
                      </label>
                      <input
                        type="text"
                        value={newFilename}
                        onChange={(e) => setNewFilename(e.target.value)}
                        placeholder="NAZOV.md"
                        className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                  </div>
                </div>
                <label className="block text-xs font-medium text-gray-400 mb-1">
                  Obsah (Markdown)
                </label>
                <textarea
                  lang="sk"
                  value={newContent}
                  onChange={(e) => setNewContent(e.target.value)}
                  placeholder="# Názov dokumentu&#10;&#10;Obsah v Markdown..."
                  className="flex-1 min-h-[200px] px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-sm text-gray-200 font-mono resize-none focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <div className="flex gap-2 mt-4">
                  <button
                    onClick={handleCreate}
                    disabled={saving || !newTitle.trim() || !newContent.trim()}
                    className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-500 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                    Uložiť
                  </button>
                  <button
                    onClick={() => setMode("browse")}
                    className="flex items-center gap-1.5 px-4 py-2 bg-gray-700 text-gray-300 rounded-lg hover:bg-gray-600 text-sm transition-colors"
                  >
                    <X size={16} /> Zrušiť
                  </button>
                </div>
              </div>
            ) : selectedDoc ? (
              mode === "edit" ? (
                <div className="flex-1 flex flex-col p-4 overflow-hidden">
                  <div className="flex items-center justify-between mb-3">
                    <h2 className="text-lg font-semibold text-gray-100">
                      {makeTitle(selectedDoc.filename)}
                    </h2>
                    <span className="text-xs text-gray-500">{selectedDoc.relative_path}</span>
                  </div>
                  <textarea
                    lang="sk"
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    className="flex-1 px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-sm text-gray-200 font-mono resize-none focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <div className="flex gap-2 mt-3">
                    <button
                      onClick={handleUpdate}
                      disabled={saving}
                      className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-500 text-sm font-medium transition-colors disabled:opacity-50"
                    >
                      {saving ? (
                        <Loader2 size={16} className="animate-spin" />
                      ) : (
                        <Save size={16} />
                      )}
                      Uložiť zmeny
                    </button>
                    <button
                      onClick={() => setMode("browse")}
                      className="flex items-center gap-1.5 px-4 py-2 bg-gray-700 text-gray-300 rounded-lg hover:bg-gray-600 text-sm transition-colors"
                    >
                      <X size={16} /> Zrušiť
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex-1 flex flex-col overflow-hidden">
                  <div className="p-4 border-b border-gray-700 flex items-center justify-between">
                    <div>
                      <h2 className="text-lg font-semibold text-gray-100">
                        {makeTitle(selectedDoc.filename)}
                      </h2>
                      <div className="text-xs text-gray-500 mt-1 flex gap-3">
                        <span>{selectedDoc.category}</span>
                        <span>{(selectedDoc.size_bytes / 1024).toFixed(1)} kB</span>
                        <span className="font-mono">{selectedDoc.relative_path}</span>
                      </div>
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => copyDoc(docContent)}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-700 text-gray-300 rounded hover:bg-gray-600 text-sm transition-colors"
                        title={isDocCopied ? "Skopírované!" : "Kopírovať obsah"}
                      >
                        {isDocCopied ? (
                          <>
                            <Check size={14} className="text-green-400" />
                            <span className="text-green-400">Skopírované</span>
                          </>
                        ) : (
                          <>
                            <Copy size={14} /> Kopírovať
                          </>
                        )}
                      </button>
                      {canWrite && (
                        <>
                          <button
                            onClick={() => {
                              setEditContent(docContent);
                              setMode("edit");
                            }}
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-700 text-gray-300 rounded hover:bg-gray-600 text-sm transition-colors"
                          >
                            <Pencil size={14} /> Upraviť
                          </button>
                          {!confirmDelete ? (
                            <button
                              onClick={() => setConfirmDelete(true)}
                              className="flex items-center gap-1.5 px-3 py-1.5 bg-red-900/30 text-red-400 rounded hover:bg-red-900/50 text-sm transition-colors"
                            >
                              <Trash2 size={14} /> Zmazať
                            </button>
                          ) : (
                            <div className="flex items-center gap-2">
                              <span className="text-xs text-red-400">Naozaj zmazať?</span>
                              <button
                                onClick={handleDelete}
                                disabled={deleting}
                                className="flex items-center gap-1 px-2 py-1 bg-red-600 text-white rounded text-xs hover:bg-red-500 disabled:opacity-50"
                              >
                                {deleting ? (
                                  <Loader2 size={12} className="animate-spin" />
                                ) : (
                                  "Áno"
                                )}
                              </button>
                              <button
                                onClick={() => setConfirmDelete(false)}
                                className="px-2 py-1 bg-gray-700 text-gray-300 rounded text-xs hover:bg-gray-600"
                              >
                                Nie
                              </button>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  </div>

                  <div className="flex-1 overflow-y-auto p-6">
                    {loadingContent ? (
                      <div className="flex items-center gap-2 text-gray-400">
                        <Loader2 size={16} className="animate-spin" /> Načítavam obsah...
                      </div>
                    ) : (
                      <div className="prose prose-sm prose-invert max-w-none prose-headings:text-white prose-p:text-gray-200 prose-strong:text-white prose-li:text-gray-200 prose-code:text-gray-200 prose-a:text-blue-400 prose-td:text-gray-200 prose-th:text-gray-100 prose-table:border prose-table:border-gray-600 prose-td:border prose-td:border-gray-700 prose-td:px-3 prose-td:py-1 prose-th:border prose-th:border-gray-600 prose-th:px-3 prose-th:py-1 prose-th:bg-gray-800">
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
                                  <CodeBlock language={match[1]}>{String(children)}</CodeBlock>
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
                    )}
                  </div>
                </div>
              )
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center text-gray-500">
                <Eye size={48} className="mb-4 text-gray-600" />
                <p className="text-sm">Vyber dokument zo zoznamu alebo vytvor nový</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

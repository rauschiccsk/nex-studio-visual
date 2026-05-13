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
 * * RAG endpoints (``/api/v1/rag/search``, ``/api/v1/rag/document``)
 *   wired up in M3 — vector search shows ranked Qdrant matches in the
 *   document list. Cross-tenant ``/stats`` panel still M8 (Audit).
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
import { KbTree } from "@/components/KbTree";
import type { KnowledgeDoc } from "@/types/knowledge";

// --- Interfaces ---

interface SearchResult {
  source_file: string;
  title: string;
  category: string;
  snippet: string;
  score: number;
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
  // selectedCategory drives the default category for "Nový" doc creation.
  // Setter is unused after the categories sidebar removal (the value is
  // restored from sessionStore at mount and not mutated thereafter).
  const [selectedCategory] = useState<string | null>(
    () => useSessionStore.getState().knowledgeCategory,
  );
  const [loading, setLoading] = useState(false);

  // View mode: 'tree' (hierarchical browser, default) or 'all'
  // (flat list of all documents — toggle "Všetky dokumenty" above tree).
  const [viewMode, setViewMode] = useState<"tree" | "all">("tree");

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

  // Search state — RAG wired in M3 (Qdrant vector search)
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null);
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
      setSearchResults(null);
      useSessionStore.getState().setKnowledgeDocPath(doc.relative_path);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Chyba pri načítaní dokumentu");
    }
    setLoadingContent(false);
  };

  // Fallback for search hits whose ``source_file`` doesn't match a doc on
  // disk — load full document straight from Qdrant chunks (e.g. orphan
  // vectors from a previous index that points at a renamed file).
  const loadDocContentByPath = async (filePath: string) => {
    setLoadingContent(true);
    setError("");
    const filename = filePath.replace(/\\/g, "/").split("/").pop() || filePath;
    try {
      const data = await api.get<{ relative_path: string; content: string }>(
        "/knowledge/documents/content",
        { params: { relative_path: filePath } },
      );
      setDocContent(data.content);
      setSelectedDoc({ relative_path: filePath, filename, category: "", size_bytes: 0 });
      setMode("browse");
      setSearchResults(null);
      useSessionStore.getState().setKnowledgeDocPath(filePath);
    } catch {
      try {
        const data = await api.get<{ content?: string; source_file?: string }>(
          "/rag/document",
          { params: { tenant: "icc", source_file: filePath } },
        );
        setDocContent(data.content || "");
        setSelectedDoc({ relative_path: filePath, filename, category: "", size_bytes: 0 });
        setMode("browse");
        setSearchResults(null);
        useSessionStore.getState().setKnowledgeDocPath(filePath);
      } catch {
        setError("Dokument sa nepodarilo načítať");
      }
    }
    setLoadingContent(false);
  };

  const doSearch = async () => {
    if (!searchQuery.trim()) {
      setSearchResults(null);
      setSearchInfo(null);
      return;
    }
    setLoading(true);
    setError("");
    setSearchInfo(null);
    try {
      const data = await api.get<{ results: SearchResult[]; count: number }>(
        "/rag/search",
        { params: { tenant: "icc", query: searchQuery } },
      );
      setSearchResults(data.results);
      setSelectedDoc(null);
      setDocContent("");
      setMode("browse");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Chyba pri vyhľadávaní");
    }
    setLoading(false);
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
        tenant: "icc",
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
        tenant: "icc",
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
        params: { relative_path: selectedDoc.relative_path, tenant: "icc" },
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
    setSearchResults(null);
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
      {/* KB Tree column — unified hierarchical browser (replaces former
          categories sidebar + documents list) */}
      <div className="w-80 bg-gray-800 border-r border-gray-700 flex flex-col min-w-0">
        {/* Header */}
        <div className="p-3 border-b border-gray-700">
          <h2 className="text-base font-semibold flex items-center gap-2 text-gray-100">
            <FolderOpen size={18} />
            Knowledge Base
          </h2>
          <div className="mt-2 flex items-center gap-1.5 text-xs text-gray-400">
            <Database size={12} />
            <span>{documents.length} dokumentov</span>
          </div>
        </div>

        {/* Toolbar — Search + Hľadať + Refresh + Strom/Všetky toggle + Nový */}
        <div className="p-3 border-b border-gray-700 flex flex-col gap-2">
          <div className="flex gap-1.5">
            <div className="flex-1 relative">
              <Search
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400"
                size={14}
              />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && doSearch()}
                placeholder="Hľadať..."
                className="w-full pl-8 pr-2 py-1.5 bg-gray-900 border border-gray-700 rounded text-xs text-gray-200 placeholder:text-gray-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <button
              onClick={doSearch}
              className="px-2 py-1.5 bg-gray-700 text-gray-300 rounded hover:bg-gray-600 text-xs transition-colors"
            >
              Hľadať
            </button>
            <button
              onClick={() => {
                setSearchQuery("");
                setSearchInfo(null);
                setSearchResults(null);
                refresh();
              }}
              className="p-1.5 bg-gray-700 text-gray-300 rounded hover:bg-gray-600 transition-colors"
              title="Obnoviť"
            >
              <RefreshCw size={14} />
            </button>
          </div>
          <div className="flex gap-1.5">
            <button
              onClick={() =>
                setViewMode((m) => (m === "tree" ? "all" : "tree"))
              }
              className={`flex-1 px-2 py-1.5 rounded text-xs font-medium transition-colors ${
                viewMode === "all"
                  ? "bg-blue-600 text-white hover:bg-blue-500"
                  : "bg-gray-700 text-gray-300 hover:bg-gray-600"
              }`}
              title="Prepínať medzi hierarchickým stromom a plochým zoznamom"
            >
              {viewMode === "tree" ? "Všetky dokumenty" : "← Strom"}
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
                className="flex items-center gap-1 px-2 py-1.5 bg-blue-600 text-white rounded hover:bg-blue-500 text-xs font-medium transition-colors"
              >
                <Plus size={14} /> Nový
              </button>
            )}
          </div>
        </div>

        {searchInfo && (
          <div className="px-3 py-2 bg-blue-900/30 border-b border-blue-800/50 text-blue-300 text-xs flex items-center justify-between">
            <span className="truncate">{searchInfo}</span>
            <button
              onClick={() => setSearchInfo(null)}
              className="text-blue-300 hover:text-blue-200 ml-2"
            >
              <X size={12} />
            </button>
          </div>
        )}

        {error && (
          <div className="px-3 py-2 bg-red-900/30 border-b border-red-800/50 text-red-400 text-xs flex items-center justify-between">
            <span className="truncate">{error}</span>
            <button onClick={() => setError("")} className="text-red-400 hover:text-red-300 ml-2">
              <X size={12} />
            </button>
          </div>
        )}

        {/* Tree / search results / flat-all list */}
        <div className="flex-1 overflow-y-auto py-1">
          {loading ? (
            <div className="p-4 flex items-center gap-2 text-gray-400 text-sm">
              <Loader2 size={14} className="animate-spin" /> Načítavam...
            </div>
          ) : searchResults !== null ? (
            searchResults.length === 0 ? (
              <div className="p-4 text-gray-500 text-xs">Žiadne výsledky</div>
            ) : (
              searchResults.map((r) => (
                <button
                  key={r.source_file}
                  onClick={() => {
                    const norm = (s: string) => s.replace(/\\/g, "/");
                    const match = documents.find(
                      (d) =>
                        d.relative_path === r.source_file ||
                        norm(d.relative_path) === norm(r.source_file) ||
                        d.relative_path.endsWith(r.source_file) ||
                        r.source_file.endsWith(d.relative_path),
                    );
                    if (match) {
                      loadDocContent(match);
                    } else {
                      loadDocContentByPath(r.source_file);
                    }
                  }}
                  className="w-full text-left p-3 border-b border-gray-700 hover:bg-gray-700/50 transition-colors"
                >
                  <div className="flex items-center gap-2">
                    <Search size={12} className="text-blue-400 flex-shrink-0" />
                    <span className="font-medium truncate text-xs text-gray-100">{r.title}</span>
                  </div>
                  <div className="text-[10px] text-gray-500 mt-1">
                    {r.category} · score: {r.score.toFixed(2)}
                  </div>
                  {r.snippet && (
                    <div className="text-[10px] text-gray-500 mt-1 line-clamp-2">{r.snippet}</div>
                  )}
                </button>
              ))
            )
          ) : viewMode === "all" ? (
            documents.length === 0 ? (
              <div className="p-4 text-gray-500 text-xs">Žiadne dokumenty</div>
            ) : (
              documents.map((doc) => {
                const project = extractProjectFromPath(doc.relative_path);
                return (
                  <button
                    key={doc.relative_path}
                    onClick={() => loadDocContent(doc)}
                    className={`w-full text-left p-2 border-b border-gray-700/50 hover:bg-gray-700/50 transition-colors ${
                      selectedDoc?.relative_path === doc.relative_path ? "bg-gray-700" : ""
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <FileText size={12} className="text-gray-400 flex-shrink-0" />
                      <span className="font-medium truncate text-xs text-gray-100">
                        {makeTitle(doc.filename)}
                      </span>
                    </div>
                    <div className="text-[10px] text-gray-500 mt-0.5">
                      {project && (
                        <span className="text-blue-400 mr-1.5">[{project}]</span>
                      )}
                      {doc.category} · {(doc.size_bytes / 1024).toFixed(1)} kB
                    </div>
                  </button>
                );
              })
            )
          ) : (
            <KbTree
              documents={documents}
              selectedPath={selectedDoc?.relative_path ?? null}
              onSelect={loadDocContent}
              hideCredentials={!isDirector}
            />
          )}
        </div>
      </div>

      {/* Main content area — viewer / editor / creator (unchanged) */}
      <div className="flex-1 flex flex-col min-w-0">
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
  );
}

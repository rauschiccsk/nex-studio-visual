import { useEffect, useState, useCallback } from "react";
import {
  listKbDocuments,
  createKbDocument,
  updateKbDocument,
  deleteKbDocument,
} from "@/services/api/kbDocuments";
import { listProjectsApi } from "@/services/api/projects";
import type { KbDocumentRead, KbDocumentCategory } from "@/types/kbDocument";
import type { ProjectRead } from "@/types";

// ─── Constants ────────────────────────────────────────────────────────────────

const CATEGORIES: { value: KbDocumentCategory | "all"; label: string }[] = [
  { value: "all", label: "Všetky" },
  { value: "standards", label: "standards" },
  { value: "decisions", label: "decisions" },
  { value: "lessons", label: "lessons" },
  { value: "patterns", label: "patterns" },
  { value: "design", label: "design" },
  { value: "behavior", label: "behavior" },
  { value: "session", label: "session" },
];

function catColor(cat: KbDocumentCategory): string {
  switch (cat) {
    case "standards": return "bg-indigo-500/20 border-indigo-500/30 text-indigo-400";
    case "decisions": return "bg-purple-500/20 border-purple-500/30 text-purple-400";
    case "lessons": return "bg-amber-500/20 border-amber-500/30 text-amber-400";
    case "patterns": return "bg-cyan-500/20 border-cyan-500/30 text-cyan-400";
    case "design": return "bg-green-500/20 border-green-500/25 text-green-400";
    case "behavior": return "bg-rose-500/20 border-rose-500/30 text-rose-400";
    case "session": return "bg-slate-700/60 border-slate-600 text-slate-400";
    default: return "bg-slate-700/60 border-slate-600 text-slate-400";
  }
}

type KbMode = "view" | "edit" | "create";
type KbTab = "documents" | "quality";
type KbScope = "global" | "project";

// ─── KnowledgeBasePage ────────────────────────────────────────────────────────

export default function KnowledgeBasePage() {
  const [kbTab, setKbTab] = useState<KbTab>("documents");
  const [scope, setScope] = useState<KbScope>("global");
  const [selectedCat, setSelectedCat] = useState<KbDocumentCategory | "all">("all");
  const [search, setSearch] = useState("");

  // Data
  const [docs, setDocs] = useState<KbDocumentRead[]>([]);
  const [loading, setLoading] = useState(false);
  const [projects, setProjects] = useState<ProjectRead[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");

  // Selected doc
  const [selected, setSelected] = useState<KbDocumentRead | null>(null);
  const [mode, setMode] = useState<KbMode>("view");

  // Edit state
  const [editTitle, setEditTitle] = useState("");
  const [editPath, setEditPath] = useState("");
  const [saving, setSaving] = useState(false);

  // Create state
  const [createTitle, setCreateTitle] = useState("");
  const [createCat, setCreateCat] = useState<KbDocumentCategory>("design");
  const [createPath, setCreatePath] = useState("");
  const [createContent, setCreateContent] = useState("");
  const [createError, setCreateError] = useState("");

  // Delete confirm
  const [deleteConfirm, setDeleteConfirm] = useState(false);

  // Upload modal
  const [showUpload, setShowUpload] = useState(false);

  const loadDocs = useCallback(() => {
    setLoading(true);
    const params: Parameters<typeof listKbDocuments>[0] = { limit: 100 };
    if (scope === "global") {
      params.project_id = null;
    } else if (selectedProjectId) {
      params.project_id = selectedProjectId;
    }
    if (selectedCat !== "all") params.doc_category = selectedCat;
    listKbDocuments(params)
      .then((res) => setDocs(res.items))
      .finally(() => setLoading(false));
  }, [scope, selectedProjectId, selectedCat]);

  useEffect(() => {
    listProjectsApi({ limit: 100 }).then((res) => setProjects(res.items));
  }, []);

  useEffect(() => {
    loadDocs();
    setSelected(null);
    setMode("view");
  }, [loadDocs]);

  const filteredDocs = search.trim()
    ? docs.filter((d) =>
        d.title.toLowerCase().includes(search.toLowerCase()) ||
        d.file_path.toLowerCase().includes(search.toLowerCase()),
      )
    : docs;

  function handleSelectDoc(doc: KbDocumentRead) {
    setSelected(doc);
    setMode("view");
    setDeleteConfirm(false);
  }

  function handleStartEdit() {
    if (!selected) return;
    setEditTitle(selected.title);
    setEditPath(selected.file_path);
    setMode("edit");
  }

  async function handleSaveEdit() {
    if (!selected) return;
    setSaving(true);
    try {
      const updated = await updateKbDocument(selected.id, { title: editTitle, file_path: editPath });
      setDocs((prev) => prev.map((d) => d.id === updated.id ? updated : d));
      setSelected(updated);
      setMode("view");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!selected) return;
    await deleteKbDocument(selected.id);
    setDocs((prev) => prev.filter((d) => d.id !== selected.id));
    setSelected(null);
    setMode("view");
    setDeleteConfirm(false);
  }

  function handleStartCreate() {
    setMode("create");
    setSelected(null);
    setCreateTitle(""); setCreateCat("design"); setCreatePath(""); setCreateContent(""); setCreateError("");
  }

  async function handleSaveCreate() {
    if (!createTitle || !createPath) { setCreateError("Názov a cesta súboru sú povinné."); return; }
    setSaving(true);
    setCreateError("");
    try {
      const payload = {
        title: createTitle,
        file_path: createPath,
        doc_category: createCat,
        project_id: scope === "project" && selectedProjectId ? selectedProjectId : null,
      };
      const doc = await createKbDocument(payload);
      setDocs((prev) => [doc, ...prev]);
      setSelected(doc);
      setMode("view");
    } catch {
      setCreateError("Nepodarilo sa vytvoriť dokument.");
    } finally {
      setSaving(false);
    }
  }

  // Auto-generate filename from title
  function autoFilename(title: string): string {
    return title.toUpperCase().replace(/\s+/g, "_").replace(/[^A-Z0-9_]/g, "") + ".md";
  }

  const indexedCount = docs.filter((d) => d.qdrant_point_id).length;

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* Sub-tabs */}
      <div className="flex items-center gap-1 px-4 pt-3 pb-0 border-b border-slate-800 flex-shrink-0 bg-slate-900/30">
        <button
          onClick={() => setKbTab("documents")}
          className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-t-lg border-b-2 transition-colors ${
            kbTab === "documents"
              ? "border-primary-500 text-primary-400 bg-primary-500/5"
              : "border-transparent text-slate-500 hover:text-slate-300"
          }`}
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          Dokumenty
        </button>
        <button
          onClick={() => setKbTab("quality")}
          className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-t-lg border-b-2 transition-colors ${
            kbTab === "quality"
              ? "border-primary-500 text-primary-400 bg-primary-500/5"
              : "border-transparent text-slate-500 hover:text-slate-300"
          }`}
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
          </svg>
          Kvalita
        </button>
      </div>

      {/* ── DOKUMENTY ── */}
      {kbTab === "documents" && (
        <div className="flex flex-1 overflow-hidden">

          {/* Left: category sidebar */}
          <div className="w-48 flex-shrink-0 flex flex-col border-r border-slate-800 bg-slate-900/40">
            {/* Scope toggle */}
            <div className="px-2 pt-2 pb-1.5 border-b border-slate-800 flex-shrink-0">
              <div className="flex rounded-lg bg-slate-800 p-0.5 gap-0.5">
                <button
                  onClick={() => setScope("global")}
                  className={`flex-1 text-[11px] font-medium py-1 rounded-md transition-colors ${
                    scope === "global" ? "bg-slate-700 text-slate-200" : "text-slate-500 hover:text-slate-400"
                  }`}
                >
                  Globálne
                </button>
                <button
                  onClick={() => setScope("project")}
                  className={`flex-1 text-[11px] font-medium py-1 rounded-md transition-colors ${
                    scope === "project" ? "bg-slate-700 text-slate-200" : "text-slate-500 hover:text-slate-400"
                  }`}
                >
                  Projektové
                </button>
              </div>
              {scope === "project" && (
                <select
                  value={selectedProjectId}
                  onChange={(e) => setSelectedProjectId(e.target.value)}
                  className="mt-1.5 w-full text-[10px] bg-slate-800 border border-slate-700 rounded px-1.5 py-1 text-primary-400 focus:outline-none focus:border-primary-500"
                >
                  <option value="">Vybrať projekt…</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              )}
            </div>

            {/* Search */}
            <div className="px-2.5 pt-2 pb-2 border-b border-slate-800">
              <div className="relative">
                <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-600 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Vector search…"
                  className="w-full pl-8 pr-3 py-1.5 text-xs rounded-lg border border-slate-700 bg-slate-800 text-slate-200 placeholder-slate-600 focus:outline-none focus:border-primary-500 transition-colors"
                />
              </div>
            </div>

            {/* Stats */}
            <div className="px-3 py-2 border-b border-slate-800/60">
              <div className="text-[10px] text-slate-600 mb-1">Qdrant stats</div>
              <div className="flex items-center gap-1.5">
                <div className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
                <span className="text-[10px] text-slate-400">
                  {docs.length} docs · {indexedCount} indexed
                </span>
              </div>
            </div>

            {/* Categories */}
            <div className="flex-1 overflow-y-auto py-2 px-2 space-y-0.5">
              <div className="text-[10px] text-slate-600 uppercase tracking-widest font-semibold px-2 pb-1">Kategórie</div>
              {CATEGORIES.map((c) => {
                const count = c.value === "all" ? docs.length : docs.filter((d) => d.doc_category === c.value).length;
                return (
                  <button
                    key={c.value}
                    onClick={() => setSelectedCat(c.value as KbDocumentCategory | "all")}
                    className={`w-full text-left px-2 py-1.5 rounded-lg text-xs flex items-center justify-between transition-colors ${
                      selectedCat === c.value
                        ? "bg-primary-600/20 text-primary-400"
                        : "text-slate-500 hover:text-slate-300 hover:bg-slate-800"
                    }`}
                  >
                    <span>{c.label}</span>
                    <span className="font-mono text-[10px] text-slate-600">{count}</span>
                  </button>
                );
              })}
            </div>

            {/* Actions */}
            <div className="border-t border-slate-800 px-2.5 py-2 space-y-1.5">
              <button
                onClick={handleStartCreate}
                className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
                Nový
              </button>
              <button
                onClick={() => setShowUpload(true)}
                className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs text-slate-500 border border-slate-700 hover:border-slate-600 rounded-lg transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                </svg>
                Upload
              </button>
            </div>
          </div>

          {/* Middle: document list */}
          <div className="w-64 flex-shrink-0 flex flex-col border-r border-slate-800">
            <div className="flex items-center justify-between px-3 h-10 border-b border-slate-800 flex-shrink-0">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-widest">Dokumenty</span>
              <span className="text-[10px] text-slate-600 font-mono">{filteredDocs.length}</span>
            </div>
            <div className="flex-1 overflow-y-auto divide-y divide-slate-800/50">
              {loading && (
                <div className="flex items-center justify-center py-8 text-slate-600 text-xs">Načítavam…</div>
              )}
              {!loading && filteredDocs.length === 0 && (
                <div className="flex items-center justify-center py-8 text-slate-700 text-xs">Žiadne dokumenty</div>
              )}
              {filteredDocs.map((doc) => (
                <button
                  key={doc.id}
                  onClick={() => handleSelectDoc(doc)}
                  className={`w-full text-left px-3 py-2.5 transition-colors ${
                    selected?.id === doc.id
                      ? "bg-primary-600/10 border-l-2 border-primary-500"
                      : "hover:bg-slate-800/50 border-l-2 border-transparent"
                  }`}
                >
                  <div className="text-xs font-medium text-slate-200 truncate">{doc.title}</div>
                  <div className="flex items-center gap-1.5 mt-0.5">
                    <span className={`text-[9px] font-mono px-1 py-0.5 rounded border ${catColor(doc.doc_category)}`}>
                      {doc.doc_category}
                    </span>
                    {doc.qdrant_point_id && (
                      <span className="text-[9px] text-green-400 font-mono">● indexed</span>
                    )}
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Right panel */}
          <div className="flex-1 flex flex-col overflow-hidden">

            {/* VIEW mode */}
            {mode === "view" && (
              <div className="flex flex-col h-full overflow-hidden">
                <div className="flex items-center gap-2 px-4 h-11 border-b border-slate-800 flex-shrink-0">
                  <svg className="w-4 h-4 text-slate-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  <span className="text-sm font-medium text-slate-300 truncate flex-1">
                    {selected ? selected.title : "—"}
                  </span>
                  {selected && (
                    <div className="flex items-center gap-1">
                      <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full border ${catColor(selected.doc_category)}`}>
                        {selected.doc_category}
                      </span>
                      <button
                        onClick={handleStartEdit}
                        className="flex items-center gap-1 px-2 py-1 text-[11px] text-slate-500 hover:text-slate-300 border border-slate-700 hover:border-slate-500 rounded transition-colors"
                      >
                        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                        </svg>
                        Upraviť
                      </button>
                      {selected.qdrant_point_id ? (
                        <span className="text-[10px] text-green-400 flex items-center gap-1 px-2">
                          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                          </svg>
                          Indexed
                        </span>
                      ) : (
                        <span className="text-[10px] text-amber-400 px-2">Not indexed</span>
                      )}
                      {!deleteConfirm ? (
                        <button
                          onClick={() => setDeleteConfirm(true)}
                          className="flex items-center gap-1 px-2 py-1 text-[11px] text-red-500/70 hover:text-red-400 border border-red-500/20 hover:border-red-500/40 rounded transition-colors"
                        >
                          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                          Zmazať
                        </button>
                      ) : (
                        <div className="flex items-center gap-1.5">
                          <span className="text-[11px] text-red-400">Naozaj?</span>
                          <button
                            onClick={handleDelete}
                            className="px-2 py-0.5 text-[11px] bg-red-600 hover:bg-red-500 text-white rounded transition-colors"
                          >
                            Áno
                          </button>
                          <button
                            onClick={() => setDeleteConfirm(false)}
                            className="px-2 py-0.5 text-[11px] bg-slate-700 hover:bg-slate-600 text-slate-300 rounded transition-colors"
                          >
                            Nie
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <div className="flex-1 overflow-y-auto p-5">
                  {!selected ? (
                    <div className="flex flex-col items-center justify-center h-full text-center">
                      <svg className="w-10 h-10 text-slate-700 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                      </svg>
                      <p className="text-sm text-slate-600">Vyber dokument zo zoznamu alebo vytvor nový</p>
                    </div>
                  ) : (
                    <div className="space-y-4 max-w-2xl">
                      <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-3">
                        <div>
                          <div className="text-xs text-slate-500 mb-0.5">Názov</div>
                          <div className="text-sm text-slate-200 font-medium">{selected.title}</div>
                        </div>
                        <div>
                          <div className="text-xs text-slate-500 mb-0.5">Cesta k súboru</div>
                          <div className="font-mono text-xs text-slate-400 break-all">{selected.file_path}</div>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <div className="text-xs text-slate-500 mb-0.5">Kategória</div>
                            <span className={`text-[10px] font-mono px-2 py-0.5 rounded border ${catColor(selected.doc_category)}`}>
                              {selected.doc_category}
                            </span>
                          </div>
                          <div>
                            <div className="text-xs text-slate-500 mb-0.5">Vytvorené</div>
                            <div className="text-xs text-slate-400">{new Date(selected.created_at).toLocaleDateString("sk-SK")}</div>
                          </div>
                        </div>
                        {selected.qdrant_collection && (
                          <div>
                            <div className="text-xs text-slate-500 mb-0.5">Qdrant kolekcia</div>
                            <div className="font-mono text-xs text-slate-400">{selected.qdrant_collection}</div>
                          </div>
                        )}
                        {selected.indexed_at && (
                          <div>
                            <div className="text-xs text-slate-500 mb-0.5">Indexované</div>
                            <div className="text-xs text-green-400">{new Date(selected.indexed_at).toLocaleString("sk-SK")}</div>
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* EDIT mode */}
            {mode === "edit" && (
              <div className="flex flex-col h-full overflow-hidden">
                <div className="flex items-center gap-3 px-4 h-11 border-b border-slate-800 flex-shrink-0">
                  <svg className="w-4 h-4 text-primary-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                  </svg>
                  <span className="text-sm font-medium text-slate-300 truncate flex-1">{selected?.title}</span>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={handleSaveEdit}
                      disabled={saving}
                      className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium bg-primary-600 hover:bg-primary-500 disabled:opacity-40 text-white rounded-lg transition-colors"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      {saving ? "Ukladám…" : "Uložiť zmeny"}
                    </button>
                    <button
                      onClick={() => setMode("view")}
                      className="flex items-center gap-1 px-3 py-1.5 text-xs text-slate-500 border border-slate-700 rounded-lg hover:border-slate-500 transition-colors"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                      Zrušiť
                    </button>
                  </div>
                </div>
                <div className="flex-1 overflow-y-auto p-5">
                  <div className="max-w-2xl space-y-4">
                    <div>
                      <label className="block text-xs font-medium text-slate-400 mb-1">Názov dokumentu</label>
                      <input
                        type="text"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-primary-500 transition-colors"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-400 mb-1">Cesta k súboru</label>
                      <input
                        type="text"
                        value={editPath}
                        onChange={(e) => setEditPath(e.target.value)}
                        className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 font-mono focus:outline-none focus:border-primary-500 transition-colors"
                      />
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* CREATE mode */}
            {mode === "create" && (
              <div className="flex flex-col h-full overflow-y-auto">
                <div className="flex items-center gap-3 px-4 h-11 border-b border-slate-800 flex-shrink-0">
                  <svg className="w-4 h-4 text-green-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  <span className="text-sm font-medium text-slate-300">Nový dokument</span>
                  <div className="ml-auto flex items-center gap-2">
                    <button
                      onClick={handleSaveCreate}
                      disabled={saving}
                      className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium bg-primary-600 hover:bg-primary-500 disabled:opacity-40 text-white rounded-lg transition-colors"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      {saving ? "Ukladám…" : "Uložiť & Index"}
                    </button>
                    <button
                      onClick={() => setMode("view")}
                      className="flex items-center gap-1 px-3 py-1.5 text-xs text-slate-500 border border-slate-700 rounded-lg hover:border-slate-500 transition-colors"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                      Zrušiť
                    </button>
                  </div>
                </div>
                <div className="p-4 space-y-3 flex-shrink-0">
                  {createError && (
                    <div className="text-xs text-red-400 rounded bg-red-500/10 border border-red-500/20 px-3 py-2">{createError}</div>
                  )}
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-slate-400 mb-1">Názov dokumentu *</label>
                      <input
                        type="text"
                        value={createTitle}
                        onChange={(e) => {
                          setCreateTitle(e.target.value);
                          if (!createPath || createPath === autoFilename(createTitle)) {
                            setCreatePath(autoFilename(e.target.value));
                          }
                        }}
                        placeholder="Popisný názov…"
                        className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-primary-500 transition-colors"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-400 mb-1">Kategória</label>
                      <select
                        value={createCat}
                        onChange={(e) => setCreateCat(e.target.value as KbDocumentCategory)}
                        className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-primary-500 transition-colors"
                      >
                        {(["design","behavior","standards","decisions","lessons","patterns","session"] as KbDocumentCategory[]).map((c) => (
                          <option key={c} value={c}>{c}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-400 mb-1">Cesta k súboru (absolútna)</label>
                    <input
                      type="text"
                      value={createPath}
                      onChange={(e) => setCreatePath(e.target.value)}
                      placeholder="/home/icc/knowledge/…/NAZOV.md"
                      className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 font-mono placeholder-slate-600 focus:outline-none focus:border-primary-500 transition-colors"
                    />
                  </div>
                </div>
                <div className="flex-1 flex flex-col px-4 pb-4 min-h-0">
                  <label className="block text-xs font-medium text-slate-400 mb-1">Obsah (Markdown) — voliteľný poznámkový blok</label>
                  <textarea
                    value={createContent}
                    onChange={(e) => setCreateContent(e.target.value)}
                    placeholder={`# ${createTitle || "Názov dokumentu"}\n\nObsah v Markdown…`}
                    className="flex-1 min-h-[200px] px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-200 font-mono resize-none focus:outline-none focus:border-primary-500 transition-colors leading-relaxed"
                  />
                </div>
              </div>
            )}

          </div>
        </div>
      )}

      {/* ── KVALITA ── */}
      {kbTab === "quality" && (
        <div className="flex-1 overflow-y-auto p-5">
          <div className="max-w-3xl mx-auto space-y-5">
            {/* Stats */}
            <div className="grid grid-cols-4 gap-3">
              <div className="rounded-xl border border-slate-800 bg-slate-900 p-3">
                <div className="text-[10px] text-slate-600 uppercase tracking-widest mb-1">Dokumenty</div>
                <div className="text-2xl font-bold text-slate-100">—</div>
                <div className="text-[10px] text-slate-500 mt-0.5">na disku</div>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-900 p-3">
                <div className="text-[10px] text-slate-600 uppercase tracking-widest mb-1">Qdrant chunks</div>
                <div className="text-2xl font-bold text-primary-400">—</div>
                <div className="text-[10px] text-slate-500 mt-0.5">indexed</div>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-900 p-3">
                <div className="text-[10px] text-slate-600 uppercase tracking-widest mb-1">Posledný scan</div>
                <div className="text-sm font-semibold text-slate-200 mt-1">—</div>
                <div className="text-[10px] text-slate-600 mt-0.5">● N/A</div>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-900 p-3">
                <div className="text-[10px] text-slate-600 uppercase tracking-widest mb-1">Issues</div>
                <div className="text-2xl font-bold text-slate-600">—</div>
                <div className="text-[10px] text-slate-500 mt-0.5">warnings</div>
              </div>
            </div>

            {/* Scan button */}
            <div className="flex items-center gap-3">
              <button className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 rounded-lg transition-colors">
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
                Spustiť scan
              </button>
              <button className="flex items-center gap-2 px-4 py-2 text-sm text-slate-500 border border-slate-700 rounded-lg hover:border-slate-600 transition-colors">
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
                Cleanup zombies
              </button>
              <span className="text-xs text-slate-600">Scan not yet implemented</span>
            </div>

            {/* Hallucinations */}
            <div className="rounded-xl border border-slate-800 bg-slate-900 overflow-hidden">
              <div className="px-4 py-2.5 border-b border-slate-800">
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-widest">Top hallucinations (zakázané pojmy)</span>
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-800">
                    <th className="px-4 py-2 text-left text-[10px] font-medium text-slate-600 uppercase">Term</th>
                    <th className="px-4 py-2 text-left text-[10px] font-medium text-slate-600 uppercase">Count</th>
                    <th className="px-4 py-2 text-left text-[10px] font-medium text-slate-600 uppercase">Severity</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/50">
                  {[
                    { term: "psycopg2", count: 0, sev: "error" },
                    { term: "asyncpg", count: 0, sev: "error" },
                    { term: "Django", count: 0, sev: "warning" },
                    { term: "Flask", count: 0, sev: "warning" },
                  ].map((r) => (
                    <tr key={r.term}>
                      <td className={`px-4 py-2 font-mono text-xs ${r.sev === "error" ? "text-red-400" : "text-amber-400"}`}>{r.term}</td>
                      <td className="px-4 py-2 text-xs text-slate-400">{r.count}</td>
                      <td className="px-4 py-2">
                        <span className={`text-[10px] border rounded px-1.5 ${r.sev === "error" ? "text-red-400 border-red-500/30" : "text-amber-400 border-amber-500/30"}`}>{r.sev}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Audit log placeholder */}
            <div className="rounded-xl border border-slate-800 bg-slate-900 overflow-hidden">
              <div className="px-4 py-2.5 border-b border-slate-800 flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-widest">Audit log</span>
                <span className="text-[10px] text-slate-600">Audit log endpoint not yet implemented</span>
              </div>
              <div className="px-4 py-6 text-center text-xs text-slate-700">
                Audit log bude dostupný po implementácii backendu.
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Upload modal */}
      {showUpload && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80">
          <div className="w-full max-w-md rounded-xl border border-slate-700 bg-slate-900 p-6 shadow-2xl">
            <div className="flex items-center justify-between mb-5">
              <h3 className="text-sm font-semibold text-slate-100">Upload dokument</h3>
              <button
                onClick={() => setShowUpload(false)}
                className="text-slate-600 hover:text-slate-300 transition-colors"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="space-y-4">
              <div className="border-2 border-dashed border-slate-700 rounded-xl p-8 text-center hover:border-primary-500/50 transition-colors cursor-pointer">
                <svg className="w-8 h-8 text-slate-600 mx-auto mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                </svg>
                <p className="text-sm text-slate-500">Klikni alebo pretiahnuť súbor sem</p>
                <p className="text-[11px] text-slate-700 mt-1">.md, .txt, .pdf — max 10 MB</p>
              </div>
              <p className="text-xs text-slate-600 text-center">File upload endpoint not yet implemented in backend.</p>
              <div className="flex gap-2 pt-1">
                <button
                  onClick={() => setShowUpload(false)}
                  className="flex-1 px-4 py-2 text-sm text-slate-400 border border-slate-700 rounded-lg hover:border-slate-600 transition-colors"
                >
                  Zavrieť
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

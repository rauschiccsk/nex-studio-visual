import { useEffect, useState, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  listCredentials,
  getCredentialContent,
  putCredentialContent,
  createCredential,
  deleteCredential,
} from "@/services/api/credentials";
import { ApiError } from "@/services/api";
import type { CredentialRead } from "@/types/credential";

type Mode = "view" | "edit" | "create";

export default function CredentialsPage() {
  const [items, setItems] = useState<CredentialRead[]>([]);
  const [loading, setLoading] = useState(false);
  const [accessError, setAccessError] = useState<string | null>(null);

  const [selected, setSelected] = useState<CredentialRead | null>(null);
  const [mode, setMode] = useState<Mode>("view");

  const [content, setContent] = useState<string>("");
  const [contentLoading, setContentLoading] = useState(false);
  const [contentError, setContentError] = useState<string | null>(null);

  const [editContent, setEditContent] = useState<string>("");
  const [saving, setSaving] = useState(false);

  const [createTitle, setCreateTitle] = useState("");
  const [createFilename, setCreateFilename] = useState("");
  const [createContent, setCreateContent] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);

  const [deleteConfirm, setDeleteConfirm] = useState(false);

  const loadList = useCallback(() => {
    setLoading(true);
    setAccessError(null);
    listCredentials()
      .then(setItems)
      .catch((err) => {
        if (err instanceof ApiError) {
          if (err.status === 401) {
            setAccessError("Pre prístup k credentials sa musíš prihlásiť.");
          } else if (err.status === 403) {
            setAccessError("Credentials vyžadujú rolu Director (ri).");
          } else {
            setAccessError(`Načítanie zlyhalo (HTTP ${err.status}).`);
          }
        } else {
          setAccessError("Sieťová chyba pri načítavaní credentials.");
        }
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadList();
  }, [loadList]);

  // Load content when a credential is selected in view mode.
  useEffect(() => {
    if (!selected || mode !== "view") {
      setContent("");
      setContentError(null);
      return;
    }
    setContentLoading(true);
    setContentError(null);
    getCredentialContent(selected.id)
      .then((res) => setContent(res.content))
      .catch((err) => {
        if (err instanceof ApiError) {
          if (err.status === 404) {
            setContentError("Súbor neexistuje na disku.");
          } else if (err.status === 422) {
            setContentError("Obsah nemožno zobraziť (binárny súbor alebo prekročený limit 5 MB).");
          } else {
            setContentError(`Načítanie zlyhalo (HTTP ${err.status}).`);
          }
        } else {
          setContentError("Načítanie zlyhalo.");
        }
      })
      .finally(() => setContentLoading(false));
  }, [selected, mode]);

  function handleSelect(c: CredentialRead) {
    setSelected(c);
    setMode("view");
    setDeleteConfirm(false);
  }

  function handleStartEdit() {
    if (!selected) return;
    setEditContent(content);
    setMode("edit");
  }

  async function handleSaveEdit() {
    if (!selected) return;
    setSaving(true);
    try {
      await putCredentialContent(selected.id, { content: editContent });
      setContent(editContent);
      setMode("view");
    } finally {
      setSaving(false);
    }
  }

  function handleStartCreate() {
    setMode("create");
    setSelected(null);
    setCreateTitle("");
    setCreateFilename("");
    setCreateContent("");
    setCreateError(null);
  }

  async function handleSaveCreate() {
    if (!createTitle.trim() || !createFilename.trim()) {
      setCreateError("Názov a filename sú povinné.");
      return;
    }
    setSaving(true);
    setCreateError(null);
    try {
      const cred = await createCredential({
        title: createTitle.trim(),
        filename: createFilename.trim(),
        content: createContent,
      });
      setItems((prev) => [cred, ...prev]);
      setSelected(cred);
      setMode("view");
    } catch (err) {
      if (err instanceof ApiError) {
        setCreateError(err.status === 409 ? "Súbor s týmto názvom už existuje." : `Vytvorenie zlyhalo (HTTP ${err.status}).`);
      } else {
        setCreateError("Vytvorenie zlyhalo.");
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!selected) return;
    await deleteCredential(selected.id);
    setItems((prev) => prev.filter((c) => c.id !== selected.id));
    setSelected(null);
    setMode("view");
    setDeleteConfirm(false);
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center gap-3 px-4 h-12 border-b border-slate-800 flex-shrink-0">
        <span className="text-sm font-medium text-slate-200">Prístupy</span>
        <span className="text-[11px] text-slate-600">
          {items.length} záznamov · /opt/data/nex-studio/credentials
        </span>
        <div className="ml-auto">
          <button
            onClick={handleStartCreate}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors"
          >
            + Nový credential
          </button>
        </div>
      </div>

      {accessError && (
        <div className="m-4 rounded border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-300">
          {accessError}
        </div>
      )}

      {!accessError && (
        <div className="flex flex-1 overflow-hidden">
          {/* Left: list */}
          <div className="w-72 flex-shrink-0 border-r border-slate-800 overflow-y-auto">
            {loading && <div className="p-4 text-sm text-slate-500">Načítavam…</div>}
            {!loading && items.length === 0 && (
              <div className="p-4 text-sm text-slate-600">Žiadne credentials.</div>
            )}
            {items.map((c) => (
              <button
                key={c.id}
                onClick={() => handleSelect(c)}
                className={`w-full text-left px-4 py-3 border-b border-slate-800/50 transition-colors ${
                  selected?.id === c.id
                    ? "bg-primary-600/10 border-l-2 border-l-primary-500"
                    : "hover:bg-slate-800/50 border-l-2 border-l-transparent"
                }`}
              >
                <div className="text-sm font-medium text-slate-200 truncate">{c.title}</div>
                <div className="text-[10px] font-mono text-slate-500 truncate mt-0.5">
                  {c.file_path.split("/").pop()}
                </div>
              </button>
            ))}
          </div>

          {/* Right: detail / edit / create */}
          <div className="flex-1 overflow-y-auto p-5">
            {mode === "view" && !selected && (
              <div className="text-sm text-slate-500">Vyber credential zo zoznamu alebo vytvor nový.</div>
            )}

            {mode === "view" && selected && (
              <div className="max-w-3xl space-y-4">
                <div className="flex items-center gap-2">
                  <h2 className="text-lg font-semibold text-slate-100 flex-1">{selected.title}</h2>
                  <button
                    onClick={handleStartEdit}
                    className="px-3 py-1.5 text-xs text-slate-400 border border-slate-700 hover:border-slate-500 rounded-lg transition-colors"
                  >
                    Upraviť obsah
                  </button>
                  {!deleteConfirm ? (
                    <button
                      onClick={() => setDeleteConfirm(true)}
                      className="px-3 py-1.5 text-xs text-red-500/80 border border-red-500/30 hover:border-red-500/50 rounded-lg transition-colors"
                    >
                      Zmazať
                    </button>
                  ) : (
                    <>
                      <span className="text-xs text-red-400">Naozaj?</span>
                      <button onClick={handleDelete} className="px-2 py-1 text-xs bg-red-600 hover:bg-red-500 text-white rounded">Áno</button>
                      <button onClick={() => setDeleteConfirm(false)} className="px-2 py-1 text-xs bg-slate-700 hover:bg-slate-600 text-slate-200 rounded">Nie</button>
                    </>
                  )}
                </div>
                <div className="text-xs font-mono text-slate-500">{selected.file_path}</div>
                <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
                  {contentLoading && <div className="text-sm text-slate-500">Načítavam obsah…</div>}
                  {contentError && <div className="text-sm text-amber-400">{contentError}</div>}
                  {!contentLoading && !contentError && (
                    <article className="prose prose-invert prose-sm max-w-none">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
                    </article>
                  )}
                </div>
              </div>
            )}

            {mode === "edit" && selected && (
              <div className="max-w-3xl space-y-3">
                <div className="flex items-center gap-2">
                  <h2 className="text-lg font-semibold text-slate-100 flex-1">{selected.title} — úprava</h2>
                  <button
                    onClick={handleSaveEdit}
                    disabled={saving}
                    className="px-3 py-1.5 text-xs font-medium bg-primary-600 hover:bg-primary-500 disabled:opacity-50 text-white rounded-lg transition-colors"
                  >
                    {saving ? "Ukladám…" : "Uložiť"}
                  </button>
                  <button
                    onClick={() => setMode("view")}
                    className="px-3 py-1.5 text-xs text-slate-400 border border-slate-700 hover:border-slate-500 rounded-lg transition-colors"
                  >
                    Zrušiť
                  </button>
                </div>
                <textarea
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  className="w-full min-h-[400px] px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-200 font-mono resize-y focus:outline-none focus:border-primary-500"
                />
              </div>
            )}

            {mode === "create" && (
              <div className="max-w-3xl space-y-3">
                <div className="flex items-center gap-2">
                  <h2 className="text-lg font-semibold text-slate-100 flex-1">Nový credential</h2>
                  <button
                    onClick={handleSaveCreate}
                    disabled={saving}
                    className="px-3 py-1.5 text-xs font-medium bg-primary-600 hover:bg-primary-500 disabled:opacity-50 text-white rounded-lg transition-colors"
                  >
                    {saving ? "Ukladám…" : "Vytvoriť"}
                  </button>
                  <button
                    onClick={() => setMode("view")}
                    className="px-3 py-1.5 text-xs text-slate-400 border border-slate-700 hover:border-slate-500 rounded-lg transition-colors"
                  >
                    Zrušiť
                  </button>
                </div>
                {createError && (
                  <div className="rounded border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">{createError}</div>
                )}
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Názov</label>
                  <input
                    type="text"
                    value={createTitle}
                    onChange={(e) => setCreateTitle(e.target.value)}
                    placeholder="Popisný názov"
                    className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-primary-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Filename (bez slash-u)</label>
                  <input
                    type="text"
                    value={createFilename}
                    onChange={(e) => setCreateFilename(e.target.value)}
                    placeholder="napr. AWS_KEYS.md"
                    className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 font-mono focus:outline-none focus:border-primary-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Obsah (Markdown)</label>
                  <textarea
                    value={createContent}
                    onChange={(e) => setCreateContent(e.target.value)}
                    className="w-full min-h-[300px] px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-200 font-mono resize-y focus:outline-none focus:border-primary-500"
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

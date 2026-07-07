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
      <div className="flex items-center gap-3 px-4 h-12 border-b border-[var(--color-border-default)] flex-shrink-0">
        <span className="text-sm font-medium text-[var(--color-text-primary)]">Prístupy</span>
        <span className="text-[11px] text-[var(--color-text-muted)]">
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
        <div className="m-4 rounded border border-[var(--color-state-warning-bg)] bg-[var(--color-state-warning-bg)] p-3 text-sm text-[var(--color-state-warning-fg)]">
          {accessError}
        </div>
      )}

      {!accessError && (
        <div className="flex flex-1 overflow-hidden">
          {/* Left: list */}
          <div className="w-72 flex-shrink-0 border-r border-[var(--color-border-default)] overflow-y-auto">
            {loading && <div className="p-4 text-sm text-[var(--color-text-muted)]">Načítavam…</div>}
            {!loading && items.length === 0 && (
              <div className="p-4 text-sm text-[var(--color-text-muted)]">Žiadne credentials.</div>
            )}
            {items.map((c) => (
              <button
                key={c.id}
                onClick={() => handleSelect(c)}
                className={`w-full text-left px-4 py-3 border-b border-[var(--color-border-default)] transition-colors ${
                  selected?.id === c.id
                    ? "bg-primary-600/10 border-l-2 border-l-primary-500"
                    : "hover:bg-[var(--color-surface-hover)] border-l-2 border-l-transparent"
                }`}
              >
                <div className="text-sm font-medium text-[var(--color-text-primary)] truncate">{c.title}</div>
                <div className="text-[10px] font-mono text-[var(--color-text-muted)] truncate mt-0.5">
                  {c.file_path.split("/").pop()}
                </div>
              </button>
            ))}
          </div>

          {/* Right: detail / edit / create */}
          <div className="flex-1 overflow-y-auto p-5">
            {mode === "view" && !selected && (
              <div className="text-sm text-[var(--color-text-muted)]">Vyber credential zo zoznamu alebo vytvor nový.</div>
            )}

            {mode === "view" && selected && (
              <div className="max-w-3xl space-y-4">
                <div className="flex items-center gap-2">
                  <h2 className="text-lg font-semibold text-[var(--color-text-primary)] flex-1">{selected.title}</h2>
                  <button
                    onClick={handleStartEdit}
                    className="px-3 py-1.5 text-xs text-[var(--color-text-secondary)] border border-[var(--color-border-default)] hover:border-[var(--color-border-strong)] rounded-lg transition-colors"
                  >
                    Upraviť obsah
                  </button>
                  {!deleteConfirm ? (
                    <button
                      onClick={() => setDeleteConfirm(true)}
                      className="px-3 py-1.5 text-xs text-[var(--color-status-error)] border border-[var(--color-state-error-bg)] hover:border-[var(--color-state-error-bg)] rounded-lg transition-colors"
                    >
                      Zmazať
                    </button>
                  ) : (
                    <>
                      <span className="text-xs text-[var(--color-status-error)]">Naozaj?</span>
                      <button onClick={handleDelete} className="px-2 py-1 text-xs bg-red-600 hover:bg-red-500 text-white rounded">Áno</button>
                      <button onClick={() => setDeleteConfirm(false)} className="px-2 py-1 text-xs bg-[var(--color-surface-active)] hover:bg-[var(--color-surface-hover)] text-[var(--color-text-primary)] rounded">Nie</button>
                    </>
                  )}
                </div>
                <div className="text-xs font-mono text-[var(--color-text-muted)]">{selected.file_path}</div>
                <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-5">
                  {contentLoading && <div className="text-sm text-[var(--color-text-muted)]">Načítavam obsah…</div>}
                  {contentError && <div className="text-sm text-[var(--color-status-error)]">{contentError}</div>}
                  {!contentLoading && !contentError && (
                    <article className="prose prose-invert prose-sm max-w-none text-[var(--color-text-primary)] prose-headings:text-[var(--color-text-primary)] prose-p:text-[var(--color-text-primary)] prose-strong:text-[var(--color-text-primary)] prose-li:text-[var(--color-text-primary)] prose-code:text-[var(--color-text-primary)]">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
                    </article>
                  )}
                </div>
              </div>
            )}

            {mode === "edit" && selected && (
              <div className="max-w-3xl space-y-3">
                <div className="flex items-center gap-2">
                  <h2 className="text-lg font-semibold text-[var(--color-text-primary)] flex-1">{selected.title} — úprava</h2>
                  <button
                    onClick={handleSaveEdit}
                    disabled={saving}
                    className="px-3 py-1.5 text-xs font-medium bg-primary-600 hover:bg-primary-500 disabled:opacity-50 text-white rounded-lg transition-colors"
                  >
                    {saving ? "Ukladám…" : "Uložiť"}
                  </button>
                  <button
                    onClick={() => setMode("view")}
                    className="px-3 py-1.5 text-xs text-[var(--color-text-secondary)] border border-[var(--color-border-default)] hover:border-[var(--color-border-strong)] rounded-lg transition-colors"
                  >
                    Zrušiť
                  </button>
                </div>
                <textarea
                  lang="sk"
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  className="w-full min-h-[400px] px-3 py-2 bg-[var(--color-canvas)] border border-[var(--color-border-default)] rounded-lg text-sm text-[var(--color-text-primary)] font-mono resize-y focus:outline-none focus:border-primary-500"
                />
              </div>
            )}

            {mode === "create" && (
              <div className="max-w-3xl space-y-3">
                <div className="flex items-center gap-2">
                  <h2 className="text-lg font-semibold text-[var(--color-text-primary)] flex-1">Nový credential</h2>
                  <button
                    onClick={handleSaveCreate}
                    disabled={saving}
                    className="px-3 py-1.5 text-xs font-medium bg-primary-600 hover:bg-primary-500 disabled:opacity-50 text-white rounded-lg transition-colors"
                  >
                    {saving ? "Ukladám…" : "Vytvoriť"}
                  </button>
                  <button
                    onClick={() => setMode("view")}
                    className="px-3 py-1.5 text-xs text-[var(--color-text-secondary)] border border-[var(--color-border-default)] hover:border-[var(--color-border-strong)] rounded-lg transition-colors"
                  >
                    Zrušiť
                  </button>
                </div>
                {createError && (
                  <div className="rounded border border-[var(--color-state-error-bg)] bg-[var(--color-state-error-bg)] p-3 text-sm text-[var(--color-state-error-fg)]">{createError}</div>
                )}
                <div>
                  <label className="block text-xs font-medium text-[var(--color-text-secondary)] mb-1">Názov</label>
                  <input
                    lang="sk"
                    type="text"
                    value={createTitle}
                    onChange={(e) => setCreateTitle(e.target.value)}
                    placeholder="Popisný názov"
                    className="w-full rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-primary-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[var(--color-text-secondary)] mb-1">Názov súboru (bez slash-u)</label>
                  <input
                    type="text"
                    value={createFilename}
                    onChange={(e) => setCreateFilename(e.target.value)}
                    placeholder="napr. AWS_KEYS.md"
                    className="w-full rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text-primary)] font-mono focus:outline-none focus:border-primary-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[var(--color-text-secondary)] mb-1">Obsah (Markdown)</label>
                  <textarea
                    lang="sk"
                    value={createContent}
                    onChange={(e) => setCreateContent(e.target.value)}
                    className="w-full min-h-[300px] px-3 py-2 bg-[var(--color-canvas)] border border-[var(--color-border-default)] rounded-lg text-sm text-[var(--color-text-primary)] font-mono resize-y focus:outline-none focus:border-primary-500"
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

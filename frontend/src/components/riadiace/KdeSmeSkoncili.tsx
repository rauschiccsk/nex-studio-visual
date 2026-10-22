/**
 * DEV-151 — „Kde sme skončili“ at the top of Riadiace centrum: why the development of this version stands and what
 * next, written by a person, read by everyone who may work on the project. The agent never gets it.
 *
 * Director 20.10.2026: „Aby v prípade, že vývoj projektu z nejakej príčiny nedokončíme (príde niečo dôležitejšie) mali
 * by sme presnú informáciu o tom, kde a ako pokračovať v projekte.“ The newest note is shown with its author and time;
 * the older ones stay one click away. Writing is optional and never holds anything up.
 */
import { useCallback, useEffect, useState } from "react";

import ErrorNote from "@/components/common/ErrorNote";
import { RESTORED_DRAFT_LABEL, draftKey, useDraft } from "@/hooks/useDraft";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { addVersionNoteApi, listVersionNotesApi, type VersionNote } from "@/services/api/versionNotes";

import { noteLine } from "@/lib/versionNote";

export default function KdeSmeSkoncili({ versionId }: { versionId: string | null }) {
  const [notes, setNotes] = useState<VersionNote[] | null>(null);
  const [loadError, setLoadError] = useState<HumanError | null>(null);
  const [writing, setWriting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<HumanError | null>(null);
  const [showOlder, setShowOlder] = useState(false);
  const draft = useDraft(draftKey("kde-sme-skoncili", versionId));

  const load = useCallback(() => {
    if (!versionId) return;
    listVersionNotesApi(versionId)
      .then((list) => {
        setNotes(list);
        setLoadError(null);
      })
      .catch((err: unknown) => setLoadError(humanizeApiError(err, "Poznámky „Kde sme skončili“ sa nepodarilo načítať")));
  }, [versionId]);

  useEffect(() => {
    setNotes(null);
    load();
  }, [load]);

  async function save() {
    if (!versionId || !draft.text.trim()) return;
    setSaving(true);
    setSaveError(null);
    try {
      const note = await addVersionNoteApi(versionId, draft.text);
      draft.clear();
      setWriting(false);
      setNotes((prev) => [note, ...(prev ?? [])]);
    } catch (err) {
      setSaveError(humanizeApiError(err, "Poznámku sa nepodarilo uložiť"));
    } finally {
      setSaving(false);
    }
  }

  if (!versionId) return null;
  const [newest, ...older] = notes ?? [];
  return (
    <section aria-label="Kde sme skončili" className="border-b border-[var(--color-border-default)] px-4 py-2 text-xs">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <span className="font-semibold text-[var(--color-text-secondary)]">Kde sme skončili: </span>
          {loadError ? (
            <ErrorNote error={loadError} />
          ) : notes === null ? (
            <span className="text-[var(--color-text-muted)]">načítavam…</span>
          ) : newest ? (
            <span className="text-[var(--color-text-primary)]">{noteLine(newest)}</span>
          ) : (
            <span className="text-[var(--color-text-muted)]">zatiaľ nikto nenapísal.</span>
          )}
        </div>
        {!writing && (
          <button
            type="button"
            onClick={() => setWriting(true)}
            className="shrink-0 text-primary-700 hover:underline dark:text-primary-400"
          >
            Napísať poznámku
          </button>
        )}
      </div>
      {older.length > 0 && (
        <div className="mt-1">
          <button
            type="button"
            onClick={() => setShowOlder((v) => !v)}
            className="text-[var(--color-text-muted)] hover:underline"
          >
            {showOlder ? "Skryť staršie poznámky" : `Staršie poznámky (${older.length})`}
          </button>
          {showOlder && (
            <ul className="mt-1 space-y-0.5 text-[var(--color-text-muted)]">
              {older.map((n) => (
                <li key={n.id}>{noteLine(n)}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {writing && (
        <div className="mt-2">
          {draft.restored && <p className="mb-1 text-[var(--color-text-muted)]">{RESTORED_DRAFT_LABEL}</p>}
          <textarea
            data-draft="kde-sme-skoncili"
            lang="sk"
            spellCheck={true}
            value={draft.text}
            onChange={(e) => draft.setText(e.target.value)}
            rows={3}
            maxLength={600}
            placeholder="Prečo stojíme a čo ďalej — jedna až tri vety."
            aria-label="Kde sme skončili"
            className="w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1 text-xs text-[var(--color-text-primary)]"
          />
          <div className="mt-1 flex gap-2">
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving || !draft.text.trim()}
              className="rounded bg-primary-600 px-2 py-1 font-medium text-white hover:bg-primary-500 disabled:opacity-50"
            >
              {saving ? "Ukladám…" : "Uložiť poznámku"}
            </button>
            <button type="button" onClick={() => setWriting(false)} className="text-[var(--color-text-muted)] hover:underline">
              Zavrieť
            </button>
          </div>
          <ErrorNote error={saveError} className="mt-1" />
        </div>
      )}
    </section>
  );
}

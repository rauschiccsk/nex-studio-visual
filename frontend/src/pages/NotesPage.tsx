// NotesPage — the signed-in person's quick notes (DEV-89). 12.10.2026 the Director: a plain text editor for notes
// jotted down quickly, which stay only until he sorts them out properly.
//
// Private: only the author reads them (kept on the server, so the same on every computer and on the phone). Several
// notes, named by their first line; saved by itself while typing, saying when. A note changed meanwhile on another
// device is never overwritten without a word: the screen says so and the person chooses which text stays. „Vyriešené“
// moves a note aside (it is deleted for good only on purpose); „Do Zásobníka“ makes it a request REQ-N of the selected
// project, once.

import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Archive, Check, ChevronDown, ChevronRight, Inbox, Loader2, Plus, RotateCcw, Trash2 } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import {
  createNote,
  deleteNote,
  listNotes,
  NoteConflict,
  noteToBacklog,
  resolveNote,
  saveNote,
  type Note,
} from "@/services/api/notes";
import { useActiveContextStore } from "@/store/activeContextStore";

/** How long the typing pauses before the note is saved. */
const SAVE_DELAY_MS = 800;

const clock = (iso: string) => new Date(iso).toLocaleTimeString("sk-SK", { hour: "2-digit", minute: "2-digit" });
const nameOf = (note: Note) => note.title || "(prázdna poznámka)";

type Status =
  | { kind: "idle" }
  | { kind: "unsaved" }
  | { kind: "saving" }
  | { kind: "saved"; at: string }
  | { kind: "error"; error: HumanError };

interface Conflict {
  id: string;
  /** The other device's text, as the server has it now. */
  theirs: Note;
}

export default function NotesPage() {
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [loadError, setLoadError] = useState<HumanError | null>(null);
  const [actionError, setActionError] = useState<HumanError | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [showResolved, setShowResolved] = useState(false);
  const [busy, setBusy] = useState(false);

  // The revision each note's text on this screen was written over, and the one save waiting to go — a save runs
  // after the previous one has finished, so every change goes over the revision the server just answered with.
  const revisions = useRef<Record<string, number>>({});
  const pending = useRef<{ id: string; body: string } | null>(null);
  const running = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const editor = useRef<HTMLTextAreaElement>(null);

  const remember = useCallback((note: Note) => {
    revisions.current[note.id] = note.revision;
    setNotes((all) => (all ? all.map((n) => (n.id === note.id ? note : n)) : all));
  }, []);

  useEffect(() => {
    listNotes()
      .then((all) => {
        for (const n of all) revisions.current[n.id] = n.revision;
        setNotes(all);
        const first = all.find((n) => !n.resolved_at);
        if (first) {
          setSelectedId(first.id);
          setText(first.body);
        }
      })
      .catch((e: unknown) => setLoadError(humanizeApiError(e, "Poznámky sa nepodarilo načítať")));
  }, []);

  const pump = useCallback(async () => {
    window.clearTimeout(timer.current);
    if (running.current) return;
    running.current = true;
    try {
      while (pending.current) {
        const { id, body } = pending.current;
        pending.current = null;
        setStatus({ kind: "saving" });
        try {
          const saved = await saveNote(id, body, revisions.current[id] ?? 1);
          remember(saved);
          if (!pending.current) setStatus({ kind: "saved", at: saved.updated_at });
        } catch (e: unknown) {
          if (e instanceof NoteConflict) {
            setConflict({ id, theirs: e.current });
            setStatus({ kind: "idle" });
            return;
          }
          pending.current ??= { id, body }; // kept for „Skúsiť znova“
          setStatus({ kind: "error", error: humanizeApiError(e, "Poznámku sa nepodarilo uložiť") });
          return;
        }
      }
    } finally {
      running.current = false;
    }
  }, [remember]);

  // Leaving the page saves what was typed.
  useEffect(() => () => void pump(), [pump]);

  function type(value: string) {
    setText(value);
    if (!selectedId || conflict?.id === selectedId) return; // the person first chooses which text stays
    pending.current = { id: selectedId, body: value };
    setStatus({ kind: "unsaved" });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void pump(), SAVE_DELAY_MS);
  }

  async function select(note: Note) {
    await pump();
    setSelectedId(note.id);
    setText(note.body);
    setStatus({ kind: "idle" });
    setActionError(null);
  }

  async function act(run: () => Promise<void>, failure: string) {
    setBusy(true);
    setActionError(null);
    try {
      await pump();
      await run();
    } catch (e: unknown) {
      setActionError(humanizeApiError(e, failure));
    } finally {
      setBusy(false);
    }
  }

  const addNote = () =>
    act(async () => {
      const note = await createNote();
      revisions.current[note.id] = note.revision;
      setNotes((all) => [note, ...(all ?? [])]);
      setSelectedId(note.id);
      setText("");
      setStatus({ kind: "idle" });
      window.setTimeout(() => editor.current?.focus());
    }, "Novú poznámku sa nepodarilo založiť");

  const selected = notes?.find((n) => n.id === selectedId) ?? null;
  const open = notes?.filter((n) => !n.resolved_at) ?? [];
  const resolved = notes?.filter((n) => n.resolved_at) ?? [];

  function keepTheirs(c: Conflict) {
    remember(c.theirs);
    setText(c.theirs.body);
    setConflict(null);
    setStatus({ kind: "saved", at: c.theirs.updated_at });
  }

  function keepMine(c: Conflict) {
    revisions.current[c.id] = c.theirs.revision; // written over the text the person has just seen
    setConflict(null);
    pending.current = { id: c.id, body: text };
    void pump();
  }

  function listItem(note: Note) {
    const active = note.id === selectedId;
    return (
      <li key={note.id}>
        <button
          type="button"
          onClick={() => void select(note)}
          aria-current={active ? "true" : undefined}
          className={`w-full rounded-lg px-3 py-2 text-left transition-colors ${
            active
              ? "bg-primary-600/10 text-[var(--color-accent-primary)]"
              : "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]"
          }`}
        >
          <span className="block truncate text-sm">{nameOf(note)}</span>
          <span className="block text-[10px] text-[var(--color-text-muted)]">
            {note.backlog ? `REQ-${note.backlog.number} · ` : ""}
            {new Date(note.updated_at).toLocaleString("sk-SK")}
          </span>
        </button>
      </li>
    );
  }

  return (
    <div className="mx-auto max-w-6xl p-4 md:p-6">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h1 className="text-base font-bold text-[var(--color-text-primary)]">Poznámky</h1>
        <button
          type="button"
          onClick={() => void addNote()}
          disabled={busy || notes === null}
          className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:opacity-50"
        >
          <Plus size={14} /> Nová poznámka
        </button>
      </div>
      <p className="mb-4 text-xs text-[var(--color-text-muted)]">
        Rýchle poznámky, kým ich nevyriešiš. Vidíš ich len ty, na každom počítači aj na mobile. Ukladajú sa samy.
      </p>

      <ErrorNote error={loadError} className="mb-3" />
      {notes === null && !loadError && <Loader2 className="h-4 w-4 animate-spin text-[var(--color-text-muted)]" />}

      {notes !== null && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-[16rem_minmax(0,1fr)]">
          <nav aria-label="Zoznam poznámok" className="min-w-0">
            {open.length === 0 && (
              <p className="px-3 py-2 text-xs text-[var(--color-text-muted)]">Žiadna otvorená poznámka.</p>
            )}
            <ul className="space-y-0.5">{open.map(listItem)}</ul>
            {resolved.length > 0 && (
              <div className="mt-3">
                <button
                  type="button"
                  aria-expanded={showResolved}
                  onClick={() => setShowResolved((v) => !v)}
                  className="flex items-center gap-1 px-3 py-1 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
                >
                  {showResolved ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                  Vyriešené ({resolved.length})
                </button>
                {showResolved && <ul className="mt-1 space-y-0.5">{resolved.map(listItem)}</ul>}
              </div>
            )}
          </nav>

          <section aria-label="Poznámka" className="min-w-0">
            {!selected ? (
              <p className="rounded-lg border border-dashed border-[var(--color-border-default)] p-6 text-center text-sm text-[var(--color-text-muted)]">
                Vyber poznámku vľavo alebo založ novú.
              </p>
            ) : (
              <>
                {conflict?.id === selected.id && (
                  <div
                    role="alert"
                    className="mb-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-800 dark:text-amber-200"
                  >
                    <p className="mb-1 font-medium">
                      Poznámka sa medzitým zmenila na inom zariadení ({clock(conflict.theirs.updated_at)}). Tvoj text
                      nie je uložený.
                    </p>
                    <p className="mb-2 whitespace-pre-wrap rounded bg-[var(--color-surface)] p-2 text-[var(--color-text-secondary)]">
                      {conflict.theirs.body || "(prázdna)"}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => keepTheirs(conflict)}
                        className="rounded border border-[var(--color-border-default)] px-2.5 py-1 text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)]"
                      >
                        Ponechať text z druhého zariadenia
                      </button>
                      <button
                        type="button"
                        onClick={() => keepMine(conflict)}
                        className="rounded bg-primary-600 px-2.5 py-1 font-medium text-white hover:bg-primary-500"
                      >
                        Uložiť môj text
                      </button>
                    </div>
                  </div>
                )}

                {selected.resolved_at && (
                  <p className="mb-2 text-xs text-[var(--color-text-muted)]">
                    Vyriešená {new Date(selected.resolved_at).toLocaleString("sk-SK")}
                    {selected.backlog && (
                      <>
                        {" "}
                        — požiadavka{" "}
                        <Link
                          to={`/projects/${selected.backlog.project_slug}/backlog`}
                          className="text-primary-400 underline hover:text-primary-300"
                        >
                          REQ-{selected.backlog.number}
                        </Link>{" "}
                        v Zásobníku projektu {selected.backlog.project_name}
                      </>
                    )}
                    .
                  </p>
                )}

                <textarea
                  ref={editor}
                  aria-label="Text poznámky — prvý riadok je jej názov"
                  data-no-draft="ukladá sa na server počas písania"
                  lang="sk"
                  spellCheck={true}
                  value={text}
                  onChange={(e) => type(e.target.value)}
                  onBlur={() => void pump()}
                  placeholder="Prvý riadok je názov poznámky…"
                  className="min-h-[45vh] w-full resize-y rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] p-3 text-sm text-[var(--color-text-primary)] focus:border-primary-500 focus:outline-none"
                />

                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
                  <span className="text-[var(--color-text-muted)]" data-testid="note-status" aria-live="polite">
                    {status.kind === "saving" && "Ukladám…"}
                    {status.kind === "unsaved" && "Neuložené zmeny…"}
                    {status.kind === "saved" && `Uložené o ${clock(status.at)}`}
                    {status.kind === "idle" && `Uložené o ${clock(selected.updated_at)}`}
                  </span>
                  {status.kind === "error" && (
                    <span className="flex flex-wrap items-center gap-2">
                      <ErrorNote error={status.error} />
                      <button type="button" onClick={() => void pump()} className="text-primary-400 underline">
                        Skúsiť znova
                      </button>
                    </span>
                  )}

                  <span className="ml-auto flex flex-wrap items-center gap-2">
                    {!selected.resolved_at ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void act(async () => {
                            remember(await resolveNote(selected.id, true));
                            setShowResolved(true); // the note stays in view, now among the resolved ones
                          }, "Poznámku sa nepodarilo označiť")
                        }
                        className="flex items-center gap-1 rounded border border-[var(--color-border-default)] px-2.5 py-1 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] disabled:opacity-50"
                      >
                        <Check size={13} /> Vyriešené
                      </button>
                    ) : (
                      <>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void act(async () => remember(await resolveNote(selected.id, false)), "Poznámku sa nepodarilo vrátiť")}
                          className="flex items-center gap-1 rounded border border-[var(--color-border-default)] px-2.5 py-1 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] disabled:opacity-50"
                        >
                          <RotateCcw size={13} /> Vrátiť medzi otvorené
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => {
                            if (!window.confirm(`Natrvalo zmazať poznámku „${nameOf(selected)}“? Vrátiť sa to nedá.`)) return;
                            void act(async () => {
                              await deleteNote(selected.id);
                              setNotes((all) => all?.filter((n) => n.id !== selected.id) ?? all);
                              setSelectedId(null);
                              setText("");
                            }, "Poznámku sa nepodarilo zmazať");
                          }}
                          className="flex items-center gap-1 rounded border border-[var(--color-border-default)] px-2.5 py-1 text-[var(--color-state-error-fg)] hover:bg-[var(--color-state-error-bg)] disabled:opacity-50"
                        >
                          <Trash2 size={13} /> Zmazať natrvalo
                        </button>
                      </>
                    )}
                    {!selected.backlog && (
                      <button
                        type="button"
                        disabled={busy || !selectedProject || !selected.title}
                        onClick={() =>
                          selectedProject &&
                          void act(
                            async () => remember(await noteToBacklog(selected.id, selectedProject.slug)),
                            "Požiadavku sa nepodarilo založiť",
                          )
                        }
                        className="flex items-center gap-1 rounded bg-primary-600 px-2.5 py-1 font-medium text-white hover:bg-primary-500 disabled:opacity-50"
                      >
                        <Inbox size={13} /> Do Zásobníka{selectedProject ? ` — ${selectedProject.name}` : ""}
                      </button>
                    )}
                  </span>
                </div>
                {!selected.backlog && !selectedProject && (
                  <p className="mt-1 text-right text-[11px] text-[var(--color-text-muted)]">
                    <Archive size={11} className="mr-1 inline" />
                    Do Zásobníka: najprv si v Projektoch vyber projekt — požiadavka patrí do jeho Zásobníka.
                  </p>
                )}
                <ErrorNote error={actionError} className="mt-2" />
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

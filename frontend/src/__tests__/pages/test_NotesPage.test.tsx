/**
 * DEV-89 — quick notes: saved by themselves while typing; a change made on another device is never overwritten
 * without a word; „Vyriešené“ moves a note aside, deleted for good only on purpose; „Do Zásobníka“ needs a project.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

const context = vi.hoisted(() => ({ selectedProject: null as null | { slug: string; name: string } }));

vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: unknown) => unknown) => selector(context),
}));
vi.mock("@/services/api/notes", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/api/notes")>();
  return {
    NoteConflict: actual.NoteConflict,
    listNotes: vi.fn(),
    createNote: vi.fn(),
    saveNote: vi.fn(),
    resolveNote: vi.fn(),
    deleteNote: vi.fn(),
    noteToBacklog: vi.fn(),
  };
});

import NotesPage from "@/pages/NotesPage";
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

const note = (over: Partial<Note>): Note => ({
  id: "n1",
  body: "Zavolať Tiborovi\nkvôli faktúram",
  title: "Zavolať Tiborovi",
  revision: 3,
  created_at: "2026-10-12T08:00:00Z",
  updated_at: "2026-10-12T08:00:00Z",
  resolved_at: null,
  backlog: null,
  ...over,
});
const OPEN = note({});
const RESOLVED = note({ id: "n2", body: "Export do CSV", title: "Export do CSV", resolved_at: "2026-10-12T09:00:00Z" });

const editor = () => screen.getByLabelText("Text poznámky — prvý riadok je jej názov") as HTMLTextAreaElement;
const at = (iso: string) => new Date(iso).toLocaleTimeString("sk-SK", { hour: "2-digit", minute: "2-digit" });

function renderPage() {
  return render(
    <MemoryRouter>
      <NotesPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.clearAllMocks();
  context.selectedProject = null;
  vi.mocked(listNotes).mockResolvedValue([OPEN, RESOLVED]);
  vi.mocked(saveNote).mockImplementation(async (id, body, revision) =>
    note({ id, body, title: body.split("\n")[0], revision: revision + 1, updated_at: "2026-10-12T10:15:00Z" }),
  );
});
afterEach(() => vi.useRealTimers());

describe("quick notes (DEV-89)", () => {
  it("opens the first open note; the resolved ones wait folded; the editor checks Slovak spelling", async () => {
    renderPage();

    expect(await screen.findByDisplayValue(/Zavolať Tiborovi/)).toBe(editor());
    expect(editor()).toHaveAttribute("lang", "sk");
    expect(editor()).toHaveAttribute("spellcheck", "true");
    expect(editor()).toHaveAttribute("data-no-draft");
    expect(screen.getByRole("button", { name: "Vyriešené (1)" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Export do CSV")).toBeNull();
  });

  it("saves by itself once the typing pauses, over the revision it was written on, and says when", async () => {
    renderPage();
    await screen.findByDisplayValue(/Zavolať Tiborovi/);

    fireEvent.change(editor(), { target: { value: "Zavolať Tiborovi\nA" } });
    fireEvent.change(editor(), { target: { value: "Zavolať Tiborovi\nAj Nazarovi" } });
    expect(screen.getByTestId("note-status")).toHaveTextContent("Neuložené zmeny…");
    expect(saveNote).not.toHaveBeenCalled();

    await act(() => vi.advanceTimersByTimeAsync(800));
    expect(saveNote).toHaveBeenCalledTimes(1);
    expect(saveNote).toHaveBeenCalledWith("n1", "Zavolať Tiborovi\nAj Nazarovi", 3);
    expect(await screen.findByText(`Uložené o ${at("2026-10-12T10:15:00Z")}`)).toBeInTheDocument();

    fireEvent.change(editor(), { target: { value: "Zavolať Tiborovi" } });
    await act(() => vi.advanceTimersByTimeAsync(800));
    expect(saveNote).toHaveBeenLastCalledWith("n1", "Zavolať Tiborovi", 4);
  });

  it("⚠️ a note changed on another device is not overwritten — the person chooses which text stays", async () => {
    const theirs = note({ body: "Z telefónu", title: "Z telefónu", revision: 4, updated_at: "2026-10-12T10:10:00Z" });
    vi.mocked(saveNote).mockRejectedValueOnce(new NoteConflict(theirs));
    renderPage();
    await screen.findByDisplayValue(/Zavolať Tiborovi/);

    fireEvent.change(editor(), { target: { value: "Z počítača" } });
    await act(() => vi.advanceTimersByTimeAsync(800));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Poznámka sa medzitým zmenila na inom zariadení");
    expect(alert).toHaveTextContent("Z telefónu");
    fireEvent.change(editor(), { target: { value: "Z počítača!" } });
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(saveNote).toHaveBeenCalledTimes(1); // nothing more is sent until the person chooses

    fireEvent.click(within(alert).getByRole("button", { name: "Uložiť môj text" }));
    await waitFor(() => expect(saveNote).toHaveBeenLastCalledWith("n1", "Z počítača!", 4));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeping the other device's text puts it into the editor", async () => {
    const theirs = note({ body: "Z telefónu", title: "Z telefónu", revision: 4 });
    vi.mocked(saveNote).mockRejectedValueOnce(new NoteConflict(theirs));
    renderPage();
    await screen.findByDisplayValue(/Zavolať Tiborovi/);
    fireEvent.change(editor(), { target: { value: "Z počítača" } });
    await act(() => vi.advanceTimersByTimeAsync(800));

    fireEvent.click(await screen.findByRole("button", { name: "Ponechať text z druhého zariadenia" }));

    expect(editor()).toHaveValue("Z telefónu");
    expect(saveNote).toHaveBeenCalledTimes(1);
  });

  it("a new note opens empty, ready to write", async () => {
    vi.mocked(createNote).mockResolvedValue(note({ id: "n3", body: "", title: "", revision: 1 }));
    renderPage();
    await screen.findByDisplayValue(/Zavolať Tiborovi/);

    fireEvent.click(screen.getByRole("button", { name: "Nová poznámka" }));

    await waitFor(() => expect(editor()).toHaveValue(""));
    expect(screen.getByRole("button", { name: /\(prázdna poznámka\)/ })).toHaveAttribute("aria-current", "true");
  });

  it("„Vyriešené“ moves the note aside and an open note cannot be deleted; a resolved one only after asking", async () => {
    vi.mocked(resolveNote).mockResolvedValue({ ...OPEN, resolved_at: "2026-10-12T10:20:00Z" });
    vi.mocked(deleteNote).mockResolvedValue();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    renderPage();
    await screen.findByDisplayValue(/Zavolať Tiborovi/);
    expect(screen.queryByRole("button", { name: /Zmazať natrvalo/ })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Vyriešené$/ }));
    await waitFor(() => expect(resolveNote).toHaveBeenCalledWith("n1", true));
    expect(await screen.findByRole("button", { name: "Vyriešené (2)" })).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(screen.getByRole("button", { name: /Zmazať natrvalo/ }));
    expect(deleteNote).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Zmazať natrvalo/ }));
    await waitFor(() => expect(deleteNote).toHaveBeenCalledWith("n1"));
    expect(confirm).toHaveBeenCalledWith("Natrvalo zmazať poznámku „Zavolať Tiborovi“? Vrátiť sa to nedá.");
    confirm.mockRestore();
  });

  it("„Do Zásobníka“ says a project must be chosen first", async () => {
    renderPage();
    await screen.findByDisplayValue(/Zavolať Tiborovi/);

    expect(screen.getByRole("button", { name: /Do Zásobníka/ })).toBeDisabled();
    expect(screen.getByText(/najprv si v Projektoch vyber projekt/)).toBeInTheDocument();
  });

  it("„Do Zásobníka“ makes the note a request of the selected project and links to it", async () => {
    context.selectedProject = { slug: "nex-inbox", name: "NEX Inbox" };
    vi.mocked(noteToBacklog).mockResolvedValue({
      ...OPEN,
      resolved_at: "2026-10-12T10:30:00Z",
      backlog: { id: "b1", project_id: "p1", project_slug: "nex-inbox", project_name: "NEX Inbox", number: 12 },
    });
    renderPage();
    await screen.findByDisplayValue(/Zavolať Tiborovi/);

    fireEvent.click(screen.getByRole("button", { name: "Do Zásobníka — NEX Inbox" }));

    await waitFor(() => expect(noteToBacklog).toHaveBeenCalledWith("n1", "nex-inbox"));
    expect(await screen.findByRole("link", { name: "REQ-12" })).toHaveAttribute("href", "/projects/nex-inbox/backlog");
    expect(screen.queryByRole("button", { name: /Do Zásobníka/ })).toBeNull();
  });

  it("what was typed is saved before another note opens", async () => {
    renderPage();
    await screen.findByDisplayValue(/Zavolať Tiborovi/);
    fireEvent.change(editor(), { target: { value: "Rozpísané" } });

    fireEvent.click(screen.getByRole("button", { name: "Vyriešené (1)" }));
    fireEvent.click(screen.getByRole("button", { name: /Export do CSV/ }));

    await waitFor(() => expect(saveNote).toHaveBeenCalledWith("n1", "Rozpísané", 3));
    await waitFor(() => expect(editor()).toHaveValue("Export do CSV"));
  });
});

/**
 * DEV-441 / DEV-443 / DEV-446 — the list under the conversation takes as little room as it can and says what it is.
 *
 * 20.11.2026, Orthodox Hermenia 0.2.0: the Staviteľ unpacked nex-app-kit 0.17.0 into private/kitsrc/ — the list had
 * 196 lines for three documents the Manažér attached and pushed the conversation off the screen. Director: „riadky …
 * tak blízko ako sa dá“, „aby bolo možné aj schovať tento zoznam“, „196 riadkov čo nepatrí sem“; then, on „Súbory pre
 * Staviteľa (287)“: „pomenovanie je zlé tam majú byť prílohy, ktoré som zadal ja, po druhé počet 287“. The heading is
 * „Prílohy“ and counts the attachments; the Staviteľ's own files wait folded under them, one line per folder, opened on
 * request and deleted at once; the heading folds the whole list and the browser remembers it.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const { listPrivateFilesApi, deletePrivateFileApi, deletePrivateFolderApi } = vi.hoisted(() => ({
  listPrivateFilesApi: vi.fn(),
  deletePrivateFileApi: vi.fn(),
  deletePrivateFolderApi: vi.fn(),
}));
vi.mock("@/services/api/projectFiles", () => ({ listPrivateFilesApi, deletePrivateFileApi, deletePrivateFolderApi }));

import PrivateFilesPanel from "@/components/controlCenter/PrivateFilesPanel";
import { FILES_FOLDED_KEY, groupPrivateFiles } from "@/components/controlCenter/privateFiles";
import type { PipelineBoard } from "@/services/api/pipeline";

const BOARD = { state: null, recent_messages: [] } as unknown as PipelineBoard;

function file(path: string, by: string | null = null) {
  return {
    path,
    size_bytes: 100,
    size_label: "100 B",
    modified_at: "2026-11-20T10:00:00Z",
    uploaded_by: by,
    uploaded_at: by ? "2026-11-18T21:33:00Z" : null,
  };
}

const ATTACHED = [
  file("private/1-Irmologion-Ucs-.docx", "zoltan"),
  file("private/Akafist-pokrova.doc", "zoltan"),
  file("private/Velka-vecerna..docx", "zoltan"),
];
const KIT = Array.from({ length: 190 }, (_, i) => file(`private/kitsrc/nex-app-kit-0.17.0/src/f${i}.ts`));
const SMOKE = [file("private/ohm-smoke/start.sh"), file("private/ohm-smoke/stop.sh")];
const LOOSE = file("private/notes.txt");
const HERMENIA = [...ATTACHED, ...KIT, LOOSE, ...SMOKE];
const listing = (files: unknown[]) => ({ files, max_bytes: 1, max_label: "" });

beforeEach(() => {
  localStorage.clear();
  listPrivateFilesApi.mockReset();
  deletePrivateFileApi.mockReset();
  deletePrivateFolderApi.mockReset();
  listPrivateFilesApi.mockResolvedValue(listing(HERMENIA));
});

/** The top lines of a list: the attachments (the first list) or, once opened, the Staviteľ's own (the second). */
const lines = (which = 0) =>
  within(screen.getAllByRole("list")[which] as HTMLElement)
    .getAllByRole("listitem")
    .filter((li) => li.parentElement?.parentElement?.tagName !== "LI");
const names = (items: HTMLElement[]) => items.map((li) => li.querySelector(".font-mono")?.textContent);
const openAgentFiles = async () =>
  fireEvent.click(await screen.findByRole("button", { name: /Pracovné súbory Staviteľa · počet súborov: 193/ }));

describe("the attachments and the Staviteľ's own files (DEV-443, DEV-446)", () => {
  it("196 files: the heading counts the three attachments, the Staviteľ's 193 wait folded apart", async () => {
    render(<PrivateFilesPanel board={BOARD} versionId="v1" />);

    expect(await screen.findByText("Prílohy (3)")).toBeInTheDocument();
    expect(names(lines())).toEqual([
      "private/1-Irmologion-Ucs-.docx",
      "private/Akafist-pokrova.doc",
      "private/Velka-vecerna..docx",
    ]);
    expect(screen.queryByText("private/kitsrc/")).toBeNull();

    await openAgentFiles();

    expect(names(lines(1))).toEqual(["private/kitsrc/", "private/notes.txt", "private/ohm-smoke/"]);
    expect(screen.getByText("počet súborov: 190 · vytvoril Staviteľ")).toBeInTheDocument();
    expect(screen.queryByText("private/kitsrc/nex-app-kit-0.17.0/src/f0.ts")).toBeNull();
  });

  it("a folder shows its files on request", async () => {
    render(<PrivateFilesPanel board={BOARD} versionId="v1" />);
    await openAgentFiles();

    fireEvent.click(screen.getByRole("button", { name: /private\/ohm-smoke\// }));

    expect(screen.getByText("private/ohm-smoke/start.sh")).toBeInTheDocument();
    expect(screen.getByText("private/ohm-smoke/stop.sh")).toBeInTheDocument();
  });

  it("a folder goes at once, after a second click that says it is the whole folder", async () => {
    deletePrivateFolderApi.mockResolvedValue(listing([...ATTACHED, LOOSE, ...SMOKE]));
    render(<PrivateFilesPanel board={BOARD} versionId="v1" />);
    await openAgentFiles();
    const kitLine = lines(1)[0] as HTMLElement;

    fireEvent.click(within(kitLine).getByRole("button", { name: "Zmazať" }));
    expect(deletePrivateFolderApi).not.toHaveBeenCalled();
    fireEvent.click(within(kitLine).getByRole("button", { name: "Naozaj zmazať priečinok" }));

    await waitFor(() => expect(deletePrivateFolderApi).toHaveBeenCalledWith("v1", "private/kitsrc/"));
    await waitFor(() => expect(screen.queryByText("private/kitsrc/")).toBeNull());
    expect(screen.getByText("Prílohy (3)")).toBeInTheDocument();
    expect(deletePrivateFileApi).not.toHaveBeenCalled();
  });

  it("only the Staviteľ's files: no attachments, the heading says none", async () => {
    listPrivateFilesApi.mockResolvedValue(listing([...SMOKE]));
    render(<PrivateFilesPanel board={BOARD} versionId="v1" />);

    expect(await screen.findByText("Prílohy (0)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pracovné súbory Staviteľa · počet súborov: 2/ })).toBeInTheDocument();
  });

  it("a person's file inside a folder still stands on its own — only the agent's are grouped", () => {
    const rows = groupPrivateFiles([file("private/kitsrc/mine.doc", "zoltan"), file("private/kitsrc/a.ts")]);

    expect(rows.map((r) => (r.kind === "file" ? r.file.path : `${r.path}:${r.files.length}`))).toEqual([
      "private/kitsrc/mine.doc",
      "private/kitsrc/:1",
    ]);
  });
});

describe("the list folds away and the browser remembers it (DEV-441)", () => {
  it("the heading folds the list to its title with the count, and opens it again", async () => {
    render(<PrivateFilesPanel board={BOARD} versionId="v1" />);
    const heading = await screen.findByRole("button", { name: /Prílohy \(3\)/ });

    fireEvent.click(heading);

    expect(heading).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.queryByRole("button", { name: /Pracovné súbory Staviteľa/ })).toBeNull();
    expect(localStorage.getItem(FILES_FOLDED_KEY)).toBe("1");

    fireEvent.click(heading);
    expect(screen.getAllByRole("list").length).toBeGreaterThan(0);
    expect(localStorage.getItem(FILES_FOLDED_KEY)).toBeNull();
  });

  it("folded once, it stays folded on the next screen and version", async () => {
    localStorage.setItem(FILES_FOLDED_KEY, "1");
    render(<PrivateFilesPanel board={BOARD} versionId="v2" />);

    expect(await screen.findByRole("button", { name: /Prílohy \(3\)/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("the rows sit as close as the text: no gap between them, a delete button no taller than a line", async () => {
    render(<PrivateFilesPanel board={BOARD} versionId="v1" />);
    await openAgentFiles();

    for (const list of screen.getAllByRole("list")) expect(list.className).not.toMatch(/\bgap-/);
    for (const button of screen.getAllByRole("button", { name: "Zmazať" })) {
      expect(button.className).not.toMatch(/\bpy-/);
      expect(button.className).toMatch(/\bleading-4\b/);
    }
  });
});

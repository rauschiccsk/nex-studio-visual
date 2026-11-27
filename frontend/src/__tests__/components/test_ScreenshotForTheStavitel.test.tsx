// DEV-527 — a screenshot for the Staviteľ by Ctrl+V or dropped on the box, as Poradca and Nápady take one: stored the
// way of „Priložiť súbor“ (private/, the line in the message), in the conversation box and in the answer box alike.
// orthodox-register 0.1.0, 27.11.2026 — the Director: „…potrebujem doplniť do chatu staviteľa možnosť pridania snímku
// obrazovky podobne ako sme to už spravili v prípade Nápadov.“
import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

const { uploadPrivateFileApi } = vi.hoisted(() => ({ uploadPrivateFileApi: vi.fn() }));
vi.mock("@/services/api/projectFiles", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  uploadPrivateFileApi,
}));

import BlockRecoveryBar from "@/components/controlCenter/BlockRecoveryBar";
import ConversationComposer from "@/components/controlCenter/ConversationComposer";
import { namedForUpload } from "@/components/controlCenter/usePasteAndDropUpload";
import { EMPTY_CLIPBOARD } from "@/lib/poradcaAttachments";
import type { PipelineBoard } from "@/services/api/pipeline";

const PNG = new Uint8Array([137, 80, 78, 71]);
const line = (name: string) => `Priložil som súbor private/${name} — nájdeš ho v projekte.`;

function paste(target: HTMLElement, files: File[], types: string[] = files.length ? ["Files"] : []) {
  return fireEvent.paste(target, { clipboardData: { files, items: [], types, getData: () => "" } });
}

function composer(props: Partial<Parameters<typeof ConversationComposer>[0]> = {}) {
  render(<ConversationComposer onRelay={vi.fn(async () => ({ deferred: false }))} versionId="v527" inputReady {...props} />);
  return screen.getByRole("textbox");
}

beforeEach(() => {
  window.localStorage.clear();
  uploadPrivateFileApi.mockReset();
  uploadPrivateFileApi.mockImplementation(async (_version: string, file: File) => ({ answer_line: line(file.name) }));
});

describe("the name a screenshot from the clipboard is stored under", () => {
  it("is the minute it was taken, not „image.png“", () => {
    const at = new Date(2026, 10, 27, 12, 5);
    expect(namedForUpload(new File([PNG], "image.png", { type: "image/png" }), at).name).toBe("snimka-2026-11-27-1205.png");
    expect(namedForUpload(new File([PNG], "", { type: "image/jpeg" }), at).name).toBe("snimka-2026-11-27-1205.jpg");
  });

  it("a file with a name of its own keeps it", () => {
    const own = new File([PNG], "Snímka obrazovky z 2026-11-27 11-43-13.png", { type: "image/png" });
    expect(namedForUpload(own)).toBe(own);
  });
});

describe("the Staviteľ's box (DEV-527)", () => {
  it("a screenshot pasted with Ctrl+V is stored and its line lands in the message", async () => {
    const box = composer();
    fireEvent.change(box, { target: { value: "Toto tlačidlo je príliš vľavo." } });

    const notTaken = !paste(box, [new File([PNG], "image.png", { type: "image/png" })]);

    await waitFor(() => expect(uploadPrivateFileApi).toHaveBeenCalledTimes(1));
    const [version, stored] = uploadPrivateFileApi.mock.calls[0] as [string, File];
    expect(version).toBe("v527");
    expect(stored.name).toMatch(/^snimka-\d{4}-\d{2}-\d{2}-\d{4}\.png$/);
    expect(notTaken).toBe(true);
    await waitFor(() => expect(box).toHaveValue(`Toto tlačidlo je príliš vľavo.\n${line(stored.name)}`));
  });

  it("text pasted from the clipboard stays text — nothing is stored or said", () => {
    const box = composer();
    const taken = paste(box, [], ["text/plain"]);
    expect(taken).toBe(true);
    expect(uploadPrivateFileApi).not.toHaveBeenCalled();
    expect(screen.queryByText(EMPTY_CLIPBOARD)).not.toBeInTheDocument();
  });

  it("a picture copied with its text is stored and the text still pastes", async () => {
    const box = composer();
    const taken = paste(box, [new File([PNG], "image.png", { type: "image/png" })], ["text/html", "Files"]);
    expect(taken).toBe(true);
    await waitFor(() => expect(uploadPrivateFileApi).toHaveBeenCalledTimes(1));
  });

  it("an empty clipboard is said, with where a screenshot is made", async () => {
    const box = composer();
    paste(box, [], []);
    expect(await screen.findByText(EMPTY_CLIPBOARD)).toBeInTheDocument();
    expect(uploadPrivateFileApi).not.toHaveBeenCalled();
  });

  it("a file dropped on the row is stored the same way", async () => {
    const box = composer();
    const row = box.parentElement as HTMLElement;
    fireEvent.dragOver(row, { dataTransfer: { files: [], items: [{ kind: "file", type: "image/png" }] } });
    fireEvent.drop(row, { dataTransfer: { files: [new File([PNG], "navrh.png", { type: "image/png" })], items: [] } });

    await waitFor(() => expect(box).toHaveValue(line("navrh.png")));
  });

  it("text written while the screenshot uploads is kept", async () => {
    let finish: (value: { answer_line: string }) => void = () => {};
    uploadPrivateFileApi.mockImplementation(() => new Promise((resolve) => (finish = resolve)));
    const box = composer();
    paste(box, [new File([PNG], "image.png", { type: "image/png" })]);
    await waitFor(() => expect(uploadPrivateFileApi).toHaveBeenCalledTimes(1));

    fireEvent.change(box, { target: { value: "Pozri snímku:" } });
    await act(async () => finish({ answer_line: line("snimka.png") }));

    expect(box).toHaveValue(`Pozri snímku:\n${line("snimka.png")}`);
  });

  it("a locked box stores nothing", () => {
    const box = composer({ frameworkBlocked: true });
    paste(box, [new File([PNG], "image.png", { type: "image/png" })]);
    expect(uploadPrivateFileApi).not.toHaveBeenCalled();
  });

  it("the attach button says a screenshot goes by Ctrl+V too", () => {
    composer();
    expect(screen.getByRole("button", { name: /Priložiť súbor/ })).toHaveAttribute("title", expect.stringContaining("Ctrl+V"));
  });
});

describe("the answer box (DEV-527)", () => {
  it("takes a pasted screenshot the same way", async () => {
    const board = {
      state: { status: "blocked", block_reason: "agent_question", current_stage: "vizual", next_action: "Odpovedz agentovi." },
    } as unknown as PipelineBoard;
    render(<BlockRecoveryBar board={board} versionId="v527" onBoard={vi.fn()} />, { wrapper: MemoryRouter });
    const box = screen.getByRole("textbox");

    paste(box, [new File([PNG], "image.png", { type: "image/png" })]);

    await waitFor(() => expect(uploadPrivateFileApi).toHaveBeenCalledTimes(1));
    const stored = (uploadPrivateFileApi.mock.calls[0] as [string, File])[1];
    await waitFor(() => expect(box).toHaveValue(line(stored.name)));
  });
});

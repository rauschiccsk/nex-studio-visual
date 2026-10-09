/**
 * DEV-44 — the Manažér attaches a file for the AI Agent from the Riadiace centrum.
 *
 * Career Asistent 0.1.0 stopped on 09.10.2026: the agent asked for a real Profesia e-mail "in private/ on
 * ANDROS" and Poradca advised `scp` over Tailscale — a terminal step. Now „Priložiť súbor“ sits in the answer box
 * and in the conversation: the file is stored in the project's private/ and the line saying where it lies lands
 * in the message being written. The list of what lies there can be read and cleaned without a terminal.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const { uploadPrivateFileApi, listPrivateFilesApi, deletePrivateFileApi } = vi.hoisted(() => ({
  uploadPrivateFileApi: vi.fn(),
  listPrivateFilesApi: vi.fn(),
  deletePrivateFileApi: vi.fn(),
}));
vi.mock("@/services/api/projectFiles", () => ({ uploadPrivateFileApi, listPrivateFilesApi, deletePrivateFileApi }));

import AttachFileButton from "@/components/riadiace/AttachFileButton";
import BlockRecoveryBar from "@/components/riadiace/BlockRecoveryBar";
import { ConversationComposer } from "@/components/riadiace/ConversationComposer";
import PrivateFilesPanel from "@/components/riadiace/PrivateFilesPanel";
import { withAttachedLine } from "@/components/riadiace/privateFiles";
import { ApiError } from "@/services/api";
import type { PipelineBoard } from "@/services/api/pipeline";

const LINE = "Priložený súbor: `private/profesia.eml` (12 kB) — leží v koreni projektu v priečinku private/, mimo gitu.";
const EML = new File(["From: agent@profesia.sk"], "profesia.eml", { type: "message/rfc822" });

function stored(path = "private/profesia.eml") {
  return {
    path,
    size_bytes: 12288,
    size_label: "12 kB",
    modified_at: "2026-10-09T18:52:00Z",
    uploaded_by: "alex",
    uploaded_at: "2026-10-09T18:52:00Z",
  };
}

function choose(file: File) {
  fireEvent.change(screen.getByTestId("attach-file-input"), { target: { files: [file] } });
}

beforeEach(() => {
  localStorage.clear();
  uploadPrivateFileApi.mockReset();
  listPrivateFilesApi.mockReset();
  deletePrivateFileApi.mockReset();
  uploadPrivateFileApi.mockResolvedValue({ file: stored(), files: [stored()], answer_line: LINE, max_bytes: 1, max_label: "" });
});

describe("AttachFileButton", () => {
  it("uploads the chosen file for this build and hands back the line for the message", async () => {
    const onAttached = vi.fn();
    render(<AttachFileButton versionId="v1" onAttached={onAttached} />);

    choose(EML);

    await waitFor(() => expect(onAttached).toHaveBeenCalledWith(LINE));
    expect(uploadPrivateFileApi).toHaveBeenCalledWith("v1", EML);
  });

  it("says why when the cockpit refuses the file, and hands back nothing", async () => {
    uploadPrivateFileApi.mockRejectedValue(
      new ApiError(409, "Súbor má viac než 25 MB, čo je najviac, čo sa dá priložiť — neuložil som ho."),
    );
    const onAttached = vi.fn();
    render(<AttachFileButton versionId="v1" onAttached={onAttached} />);

    choose(EML);

    expect(await screen.findByText(/Súbor má viac než 25 MB/)).toBeInTheDocument();
    expect(onAttached).not.toHaveBeenCalled();
  });
});

describe("the line lands in the message being written", () => {
  it("on its own line after what is already there, or alone in an empty box", () => {
    expect(withAttachedLine("Tu je e-mail. Meno v oslovení: Zoltán.  \n", LINE)).toBe(
      `Tu je e-mail. Meno v oslovení: Zoltán.\n${LINE}`,
    );
    expect(withAttachedLine("", LINE)).toBe(LINE);
  });

  it("in the answer to the agent's question", async () => {
    const board = {
      state: { status: "blocked", block_reason: "agent_question", current_stage: "programovanie", next_action: "" },
    } as unknown as PipelineBoard;
    render(<BlockRecoveryBar board={board} versionId="v1" onBoard={vi.fn()} />);
    const box = screen.getByPlaceholderText(/Tvoja odpoveď/);
    fireEvent.change(box, { target: { value: "Posielam e-mail." } });

    fireEvent.click(screen.getByRole("button", { name: /Priložiť súbor/ }));
    choose(EML);

    await waitFor(() => expect(box).toHaveValue(`Posielam e-mail.\n${LINE}`));
  });

  it("in the conversation with the agent", async () => {
    render(<ConversationComposer onRelay={vi.fn(async () => ({ deferred: false }))} versionId="v1" />);

    choose(EML);

    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(LINE));
  });
});

describe("PrivateFilesPanel", () => {
  const boardWith = (events: number) =>
    ({
      state: null,
      recent_messages: Array.from({ length: events }, (_, i) => ({ id: `m${i}`, seq: i, payload: { private_file: {} } })),
    }) as unknown as PipelineBoard;

  it("shows nothing while private/ is empty", async () => {
    listPrivateFilesApi.mockResolvedValue({ files: [], max_bytes: 1, max_label: "" });
    const { container } = render(<PrivateFilesPanel board={boardWith(0)} versionId="v1" />);

    await waitFor(() => expect(listPrivateFilesApi).toHaveBeenCalledWith("v1"));
    expect(container).toBeEmptyDOMElement();
  });

  it("lists what lies there — who attached it, or that the agent made it", async () => {
    listPrivateFilesApi.mockResolvedValue({
      files: [stored(), { ...stored("private/derived.txt"), size_label: "1 kB", uploaded_by: null, uploaded_at: null }],
      max_bytes: 1,
      max_label: "",
    });
    render(<PrivateFilesPanel board={boardWith(1)} versionId="v1" />);

    expect(await screen.findByText("Súbory pre agenta (2)")).toBeInTheDocument();
    expect(screen.getByText("private/profesia.eml")).toBeInTheDocument();
    expect(screen.getByText(/12 kB · nahral alex/)).toBeInTheDocument();
    expect(screen.getByText("1 kB · vytvoril agent")).toBeInTheDocument();
  });

  it("deletes only after a second click that says what it does", async () => {
    listPrivateFilesApi.mockResolvedValue({ files: [stored()], max_bytes: 1, max_label: "" });
    deletePrivateFileApi.mockResolvedValue({ files: [], max_bytes: 1, max_label: "" });
    render(<PrivateFilesPanel board={boardWith(1)} versionId="v1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Zmazať" }));
    expect(deletePrivateFileApi).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Naozaj zmazať" }));

    await waitFor(() => expect(deletePrivateFileApi).toHaveBeenCalledWith("v1", "private/profesia.eml"));
    await waitFor(() => expect(screen.queryByText("private/profesia.eml")).not.toBeInTheDocument());
  });

  it("reloads when the board gains an attach or delete record — another tab's upload shows too", async () => {
    listPrivateFilesApi.mockResolvedValue({ files: [], max_bytes: 1, max_label: "" });
    const { rerender } = render(<PrivateFilesPanel board={boardWith(0)} versionId="v1" />);
    await waitFor(() => expect(listPrivateFilesApi).toHaveBeenCalledTimes(1));

    listPrivateFilesApi.mockResolvedValue({ files: [stored()], max_bytes: 1, max_label: "" });
    rerender(<PrivateFilesPanel board={boardWith(1)} versionId="v1" />);

    expect(await screen.findByText("private/profesia.eml")).toBeInTheDocument();
    expect(listPrivateFilesApi).toHaveBeenCalledTimes(2);
  });
});

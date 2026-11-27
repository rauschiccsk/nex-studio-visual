// DEV-534 — a screenshot pasted into the conversation with the Staviteľ as in Nápady: a small preview above the field,
// removable, sent with the message (Poslať and Uprav) as base64, a small preview at the message in the thread — never
// a file in the project. orthodox-register 0.1.0, 27.11.2026 — the Director: „Chcel som aby to bolo vložené presne
// tak ako v nápadoch, kde vidno malý obrázok priamo v chate…“
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

const { ws } = vi.hoisted(() => ({ ws: { board: null as unknown } }));

vi.mock("@/services/api/screenshots", () => ({ getScreenshotLimitsApi: vi.fn(), getScreenshotApi: vi.fn() }));
vi.mock("@/services/api/pipeline", async (orig) => ({
  ...(await orig<typeof import("@/services/api/pipeline")>()),
  relayPipelineMessageApi: vi.fn(),
  postPipelineActionApi: vi.fn(),
  getCiStatusApi: vi.fn().mockResolvedValue({ stav: "unknown", detail: "—", sha: null }),
}));
vi.mock("@/hooks/usePipelineWs", () => ({
  usePipelineWs: () => ({
    board: ws.board,
    activity: [],
    reconnecting: false,
    error: null,
    accessDenied: false,
    versionGone: false,
    setBoard: vi.fn(),
  }),
}));
vi.mock("@/store/authStore", () => {
  const auth = { user: { role: "ri", username: "admin" } };
  return { useAuthStore: (selector: (s: typeof auth) => unknown) => selector(auth) };
});
vi.mock("@/store/activeContextStore", () => {
  const context = {
    selectedProject: { slug: "orthodox-register", name: "Orthodox Register" },
    selectedVersion: { versionId: "v-534", versionNumber: "0.1.0" },
  };
  return { useActiveContextStore: (selector: (s: typeof context) => unknown) => selector(context) };
});
vi.mock("@/hooks/useVizualPreview", () => ({
  useVizualPreview: () => ({ preview: null, restarting: false, error: null, restart: vi.fn() }),
}));

import ConversationComposer from "@/components/controlCenter/ConversationComposer";
import ConversationThread from "@/components/controlCenter/ConversationThread";
import { notAnImage } from "@/components/controlCenter/useScreenshots";
import { EMPTY_CLIPBOARD } from "@/lib/poradcaAttachments";
import ControlCenterPage from "@/pages/ControlCenterPage";
import {
  postPipelineActionApi,
  relayPipelineMessageApi,
  type PipelineBoard,
  type PipelineMessage,
} from "@/services/api/pipeline";
import { getScreenshotApi, getScreenshotLimitsApi } from "@/services/api/screenshots";

const PNG = new Uint8Array([137, 80, 78, 71]);
const LIMITS = {
  attachment_max_bytes: 5 * 1024 * 1024,
  attachments_max_count: 5,
  attachments_max_total_bytes: 15 * 1024 * 1024,
  attachment_types: ["image/png", "image/jpeg", "image/webp", "image/gif"],
};
const shot = (name = "image.png") => new File([PNG], name, { type: "image/png" });

function paste(target: HTMLElement, files: File[], types: string[] = files.length ? ["Files"] : []) {
  return fireEvent.paste(target, { clipboardData: { files, items: [], types, getData: () => "" } });
}

function composer(props: Partial<Parameters<typeof ConversationComposer>[0]> = {}) {
  const onRelay = vi.fn(async () => ({ deferred: false }));
  const onUprav = vi.fn(async () => undefined);
  render(<ConversationComposer onRelay={onRelay} onUprav={onUprav} versionId="v-534" inputReady {...props} />);
  return { box: screen.getByRole("textbox"), onRelay, onUprav };
}

const previews = () => screen.queryAllByRole("img");

beforeEach(() => {
  window.localStorage.clear();
  URL.createObjectURL = vi.fn(() => "blob:nahled");
  URL.revokeObjectURL = vi.fn();
  vi.mocked(getScreenshotLimitsApi).mockReset().mockResolvedValue(LIMITS);
  vi.mocked(getScreenshotApi).mockReset().mockResolvedValue(new Blob([PNG], { type: "image/png" }));
});
afterEach(() => cleanup());

describe("the Staviteľ's box takes a screenshot as Nápady do (DEV-534)", () => {
  it("Ctrl+V shows a small preview and sends nothing yet; Poslať sends it with the text and the preview goes", async () => {
    const { box, onRelay } = composer();
    await waitFor(() => expect(getScreenshotLimitsApi).toHaveBeenCalledWith("v-534"));

    expect(paste(box, [shot()])).toBe(false);
    expect(await screen.findByRole("img", { name: /^Snímka 1 .*\.png$/ })).toHaveAttribute("src", "blob:nahled");
    expect(onRelay).not.toHaveBeenCalled();

    fireEvent.change(box, { target: { value: "Toto tlačidlo posuň doprava." } });
    fireEvent.click(screen.getByRole("button", { name: /Poslať/ }));

    await waitFor(() => expect(onRelay).toHaveBeenCalledTimes(1));
    const [text, images] = onRelay.mock.calls[0] as unknown as [string, File[]];
    expect(text).toBe("Toto tlačidlo posuň doprava.");
    expect(images.map((f) => f.type)).toEqual(["image/png"]);
    await waitFor(() => expect(previews()).toHaveLength(0));
  });

  it("Uprav carries the screenshots too", async () => {
    const { box, onUprav } = composer({ offersUprav: true, upravLabel: "Uprav" });
    await waitFor(() => expect(getScreenshotLimitsApi).toHaveBeenCalled());
    paste(box, [shot()]);
    await screen.findByRole("img");
    fireEvent.change(box, { target: { value: "Prerob farbu." } });

    fireEvent.click(screen.getByRole("button", { name: "Uprav" }));

    await waitFor(() => expect(onUprav).toHaveBeenCalledTimes(1));
    expect((onUprav.mock.calls[0] as unknown as [string, File[]])[1]).toHaveLength(1);
  });

  it("a preview is removed with its ×, and then nothing goes", async () => {
    const { box, onRelay } = composer();
    await waitFor(() => expect(getScreenshotLimitsApi).toHaveBeenCalled());
    paste(box, [shot()]);
    const preview = await screen.findByRole("img");

    fireEvent.click(screen.getByRole("button", { name: `Odobrať ${preview.getAttribute("alt")}` }));
    fireEvent.change(box, { target: { value: "Bez snímky." } });
    fireEvent.click(screen.getByRole("button", { name: /Poslať/ }));

    await waitFor(() => expect(onRelay).toHaveBeenCalledWith("Bez snímky.", []));
  });

  it("text pasted from the clipboard stays text", async () => {
    const { box } = composer();
    await waitFor(() => expect(getScreenshotLimitsApi).toHaveBeenCalled());
    expect(paste(box, [], ["text/plain"])).toBe(true);
    expect(previews()).toHaveLength(0);
    expect(screen.queryByText(EMPTY_CLIPBOARD)).not.toBeInTheDocument();
  });

  it("an empty clipboard is said", async () => {
    const { box } = composer();
    await waitFor(() => expect(getScreenshotLimitsApi).toHaveBeenCalled());
    paste(box, [], []);
    expect(await screen.findByText(EMPTY_CLIPBOARD)).toBeInTheDocument();
  });

  it("a screenshot dropped on the row is taken; a file that is no image is sent to „Priložiť súbor“", async () => {
    const { box } = composer();
    await waitFor(() => expect(getScreenshotLimitsApi).toHaveBeenCalled());
    const row = box.parentElement as HTMLElement;

    fireEvent.drop(row, { dataTransfer: { files: [shot("navrh.png")], items: [] } });
    expect(await screen.findByRole("img", { name: "navrh.png" })).toBeInTheDocument();

    const invoice = new File(["%PDF"], "faktura.pdf", { type: "application/pdf" });
    fireEvent.drop(row, { dataTransfer: { files: [invoice], items: [] } });
    expect(await screen.findByText(notAnImage("faktura.pdf"))).toBeInTheDocument();
    expect(previews()).toHaveLength(1);
  });

  it("a locked box takes nothing — neither pasted nor dropped on its row", async () => {
    const { box } = composer({ frameworkBlocked: true });
    await waitFor(() => expect(getScreenshotLimitsApi).toHaveBeenCalled());
    paste(box, [shot()]);
    fireEvent.drop(box.parentElement as HTMLElement, { dataTransfer: { files: [shot("navrh.png")], items: [] } });
    await act(async () => {});
    expect(previews()).toHaveLength(0);
  });

  it("the field says Ctrl+V pastes a screenshot", async () => {
    composer({ atVizual: true });
    expect(screen.getByPlaceholderText(/Ctrl\+V vloží snímku/)).toBeInTheDocument();
  });
});

describe("the thread shows the screenshots at the message (DEV-534)", () => {
  it("as small previews fetched from the version's conversation", async () => {
    const message = {
      id: "m1",
      version_id: "v-534",
      stage: "vizual",
      author: "manazer",
      recipient: "ai_agent",
      kind: "question",
      content: "Pozri snímku.",
      status: "sent",
      seq: 1,
      created_at: "2026-11-27T12:00:00Z",
      payload: { images: [{ id: "a".repeat(32), name: "Snímka 1.png", mime: "image/png", size_bytes: 4 }] },
    } as unknown as PipelineMessage;

    render(
      <MemoryRouter>
        <ConversationThread messages={[message]} activity={[]} working={false} versionId="v-534" />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("img", { name: /Snímka 1\.png/ })).toHaveAttribute("src", "blob:nahled");
    expect(getScreenshotApi).toHaveBeenCalledWith("v-534", "a".repeat(32));
  });
});

describe("Riadiace centrum sends them as base64, with the message and with Uprav (DEV-534)", () => {
  const at = (stage: string) =>
    ({
      state: {
        current_stage: stage,
        status: "awaiting_manazer",
        block_reason: null,
        flow_type: "new_version",
        next_action: "",
        current_actor: "ai_agent",
      },
      // At Vizuál the box is the change channel and sends by Poslať; elsewhere it carries „Uprav“ too (DEV-297).
      available_actions: ["uprav", "ask"],
      action_labels: { uprav: "Uprav" },
      recent_messages: [],
    }) as unknown as PipelineBoard;

  async function page(stage = "vizual") {
    const board = at(stage);
    ws.board = board;
    vi.mocked(relayPipelineMessageApi).mockReset().mockResolvedValue({ deferred: false, board });
    vi.mocked(postPipelineActionApi).mockReset().mockResolvedValue(board);
    render(
      <MemoryRouter>
        <ControlCenterPage />
      </MemoryRouter>,
    );
    const box = await screen.findByPlaceholderText(/Ctrl\+V vloží snímku/);
    await waitFor(() => expect(getScreenshotLimitsApi).toHaveBeenCalled());
    paste(box, [shot()]);
    await screen.findByRole("img", { name: /^Snímka 1/ });
    fireEvent.change(box, { target: { value: "Pozri." } });
    return box;
  }

  it("Poslať", async () => {
    await page();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Poslať/ }));
    });
    await waitFor(() => expect(relayPipelineMessageApi).toHaveBeenCalledTimes(1));
    const [version, text, uploads] = vi.mocked(relayPipelineMessageApi).mock.calls[0]!;
    expect([version, text]).toEqual(["v-534", "Pozri."]);
    expect(uploads).toEqual([{ name: expect.stringMatching(/^Snímka 1 /), data: btoa(String.fromCharCode(...PNG)) }]);
  });

  it("Uprav", async () => {
    await page("programovanie");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Uprav" }));
    });
    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledTimes(1));
    const [, request] = vi.mocked(postPipelineActionApi).mock.calls[0]!;
    expect(request).toEqual({
      action: "uprav",
      payload: { comment: "Pozri.", images: [{ name: expect.stringMatching(/^Snímka 1 /), data: btoa(String.fromCharCode(...PNG)) }] },
    });
  });
});

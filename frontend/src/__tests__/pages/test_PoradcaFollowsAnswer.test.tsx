/**
 * DEV-25 — while Poradca answers, the conversation follows the new text.
 *
 * Director 08.10.2026: „ak poradca píše neposúva hore obrazovku ako je to napríklad v Riadiacom centre. Ak
 * chcem čítať čo píše musím ja skrolovať obrazovku." The steps („Čítam …") and the answer arrived below the
 * bottom edge; nothing scrolled. Riadiace centrum follows its thread; Poradca did not.
 *
 * Follows only while the Manažér is at the end: someone who scrolled up to re-read an earlier answer is not
 * pulled away by every new step. His own question always brings him to the end.
 *
 * jsdom has no layout — the thread's height and scroll position are stood in for on the element itself.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const api = vi.hoisted(() => ({
  getPoradcaStatusApi: vi.fn(),
  getPoradcaContextApi: vi.fn(),
  listPoradcaConversationsApi: vi.fn(),
  createPoradcaConversationApi: vi.fn(),
  getPoradcaConversationApi: vi.fn(),
  askPoradcaApi: vi.fn(),
  setPoradcaScopeApi: vi.fn(),
  renamePoradcaConversationApi: vi.fn(),
  deletePoradcaConversationApi: vi.fn(),
  stopPoradcaApi: vi.fn(),
  newVersionFromPoradcaApi: vi.fn(),
  buildPoradcaWsUrl: vi.fn(() => "ws://test/ws"),
}));
vi.mock("@/services/api/poradca", () => api);
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ selectedProject: { slug: "demo", name: "Demo" }, selectedVersion: null, setSelectedVersion: vi.fn() }),
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ token: "t", user: { id: "me", username: "tibor" } }),
}));

class FakeSocket {
  static last: FakeSocket | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  constructor() {
    FakeSocket.last = this;
  }
  close() {}
}
vi.stubGlobal("WebSocket", FakeSocket);

import PoradcaPage from "@/pages/PoradcaPage";

const QUESTION = { id: "m1", author: "human", content: "Prečo agent stojí?", steps: [], status: "done" };
function answer(status: "running" | "done", content = "") {
  return {
    id: "m2",
    author: "poradca",
    content,
    steps: [],
    status,
    model: null,
    input_tokens: null,
    output_tokens: null,
    cost: null,
    duration_seconds: null,
    error: null,
    created_at: "2026-10-08T10:00:01Z",
    finished_at: null,
    instruction: null,
    new_version_request: null,
    captured_version_id: null,
  };
}
function conversation(messages: unknown[]) {
  return {
    id: "c1",
    project_id: "p1",
    version_id: null,
    version_number: null,
    author_id: "me",
    author_name: "Tibor",
    title: "Prečo agent stojí?",
    running: messages.some((m) => (m as { status: string }).status === "running"),
    created_at: "2026-10-08T10:00:00Z",
    updated_at: "2026-10-08T10:00:00Z",
    messages,
  };
}

/** The thread's geometry: how tall its content is, how much of it is visible, where it is scrolled to. */
const geo = { scrollHeight: 1000, clientHeight: 300, scrollTop: 0 };

function thread(): HTMLElement {
  const el = screen.getByRole("log", { name: "Rozhovor s Poradcom" });
  if (!(el as HTMLElement & { __geo?: boolean }).__geo) {
    Object.defineProperty(el, "scrollHeight", { configurable: true, get: () => geo.scrollHeight });
    Object.defineProperty(el, "clientHeight", { configurable: true, get: () => geo.clientHeight });
    Object.defineProperty(el, "scrollTop", {
      configurable: true,
      get: () => geo.scrollTop,
      set: (v: number) => {
        geo.scrollTop = v;
      },
    });
    (el as HTMLElement & { __geo?: boolean }).__geo = true;
  }
  return el;
}

/** He scrolls the thread himself — the browser fires `scroll`. */
function scrollTo(top: number) {
  geo.scrollTop = top;
  fireEvent.scroll(thread());
}

function socketSends(event: object) {
  act(() => FakeSocket.last?.onmessage?.({ data: JSON.stringify(event) }));
}

async function openRunningConversation() {
  render(
    <MemoryRouter initialEntries={["/poradca?c=c1"]}>
      <Routes>
        <Route path="/poradca" element={<PoradcaPage />} />
      </Routes>
    </MemoryRouter>,
  );
  thread(); // geometry in place before the conversation arrives
  expect(await screen.findByText("Prečo agent stojí?", { selector: "div" })).toBeInTheDocument();
  await waitFor(() => expect(FakeSocket.last?.onmessage).toBeTruthy());
}

beforeEach(() => {
  Object.values(api).forEach((f) => "mockReset" in f && f.mockReset());
  api.buildPoradcaWsUrl.mockReturnValue("ws://test/ws");
  api.getPoradcaStatusApi.mockResolvedValue({ ready: true, problems: [], running: 1, max_concurrent: 3 });
  api.getPoradcaContextApi.mockResolvedValue({ project_id: "p1", project_slug: "demo", project_name: "Demo", versions: [] });
  api.listPoradcaConversationsApi.mockResolvedValue([conversation([QUESTION, answer("running")])]);
  api.getPoradcaConversationApi.mockResolvedValue(conversation([QUESTION, answer("running")]));
  Object.assign(geo, { scrollHeight: 1000, clientHeight: 300, scrollTop: 0 });
  FakeSocket.last = null;
  window.localStorage.clear();
});

describe("DEV-25 — the Poradca conversation follows what Poradca writes", () => {
  it("an opened conversation shows its end", async () => {
    await openRunningConversation();
    await waitFor(() => expect(geo.scrollTop).toBe(1000));
  });

  it("while Poradca answers, every new step brings the view down", async () => {
    await openRunningConversation();
    await waitFor(() => expect(geo.scrollTop).toBe(1000));
    geo.scrollHeight = 1300;
    socketSends({ type: "step", message_id: "m2", tool: "read_file", target: "backend/main.py" });
    await waitFor(() => expect(geo.scrollTop).toBe(1300));
    geo.scrollHeight = 1600;
    socketSends({ type: "step", message_id: "m2", tool: "read_file", target: "backend/x.py" });
    await waitFor(() => expect(geo.scrollTop).toBe(1600));
  });

  it("the finished answer brings the view down", async () => {
    await openRunningConversation();
    await waitFor(() => expect(geo.scrollTop).toBe(1000));
    api.getPoradcaConversationApi.mockResolvedValue(conversation([QUESTION, answer("done", "Agent čaká na test.")]));
    geo.scrollHeight = 1800;
    socketSends({ type: "finished", message_id: "m2" });
    expect(await screen.findByText("Agent čaká na test.")).toBeInTheDocument();
    await waitFor(() => expect(geo.scrollTop).toBe(1800));
  });

  it("scrolled up to re-read: a new step does not pull him away", async () => {
    await openRunningConversation();
    await waitFor(() => expect(geo.scrollTop).toBe(1000));
    scrollTo(100);
    geo.scrollHeight = 1300;
    socketSends({ type: "step", message_id: "m2", tool: "read_file", target: "backend/main.py" });
    await screen.findByText(/backend\/main\.py/);
    expect(geo.scrollTop).toBe(100);
  });

  it("scrolled up in one conversation, another one opens at its end", async () => {
    api.listPoradcaConversationsApi.mockResolvedValue([
      conversation([QUESTION, answer("running")]),
      { ...conversation([]), id: "c2", title: "Čo je v UAT?" },
    ]);
    api.getPoradcaConversationApi.mockImplementation(async (id: string) =>
      id === "c2"
        ? { ...conversation([{ ...QUESTION, id: "m5", content: "Čo je v UAT?" }, answer("done", "UAT je zelené.")]), id: "c2" }
        : conversation([QUESTION, answer("running")]),
    );
    await openRunningConversation();
    await waitFor(() => expect(geo.scrollTop).toBe(1000));
    scrollTo(100);
    geo.scrollHeight = 900;
    fireEvent.click(screen.getByText("Čo je v UAT?", { selector: "span, button, p" }));
    expect(await screen.findByText("UAT je zelené.")).toBeInTheDocument();
    await waitFor(() => expect(geo.scrollTop).toBe(900));
  });

  it("his own question brings the view down even when he had scrolled up", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(conversation([QUESTION, answer("done", "Hotovo.")]));
    await openRunningConversation();
    await waitFor(() => expect(geo.scrollTop).toBe(1000));
    scrollTo(100);
    api.askPoradcaApi.mockResolvedValue(
      conversation([QUESTION, answer("done", "Hotovo."), { ...QUESTION, id: "m3", content: "A teraz?" }, { ...answer("running"), id: "m4" }]),
    );
    geo.scrollHeight = 1400;
    fireEvent.change(screen.getByPlaceholderText(/Opýtaj sa Poradcu/), { target: { value: "A teraz?" } });
    fireEvent.click(screen.getByRole("button", { name: /Opýtať sa/ }));
    await waitFor(() => expect(geo.scrollTop).toBe(1400));
  });
});

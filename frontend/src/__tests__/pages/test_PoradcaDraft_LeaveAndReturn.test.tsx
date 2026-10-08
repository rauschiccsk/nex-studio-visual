/**
 * DEV-24 — a half-written question for Poradca survives a trip to another screen.
 *
 * Director 08.10.2026: he started typing a question for Poradca, clicked over to Riadiace centrum to look
 * something up, came back — and the question was gone. Two causes on that path: the question box held its
 * text only in component state (gone when the route unmounts the page), and the sidebar opens bare
 * `/poradca`, so even the conversation he was in did not reopen.
 *
 * These tests walk his path with the REAL sidebar: type, „Riadiace centrum", „Poradca", look at the box.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";

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

const ctx = vi.hoisted(() => ({
  state: {
    selectedProject: { slug: "demo", name: "Demo" } as { slug: string; name: string } | null,
    selectedVersion: null as { versionId: string; versionNumber: string } | null,
    setSelectedVersion: vi.fn(),
    setSelectedProject: vi.fn(),
  },
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) => sel(ctx.state),
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) =>
    sel({ token: "t", user: { id: "me", username: "tibor", role: "ri" }, logout: vi.fn() }),
}));

class SilentSocket {
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  close() {}
}
vi.stubGlobal("WebSocket", SilentSocket);

import PoradcaPage from "@/pages/PoradcaPage";
import Sidebar from "@/components/layout/Sidebar";

const BOX = /Opýtaj sa Poradcu/;
const RESTORED = "Obnovený rozpísaný text — pokračuj, alebo ho prepíš.";

function conversation(id: string, title: string) {
  return {
    id,
    project_id: "p1",
    version_id: null,
    version_number: null,
    author_id: "me",
    author_name: "Tibor",
    title,
    running: false,
    created_at: "2026-10-08T10:00:00Z",
    updated_at: "2026-10-08T10:00:00Z",
    messages: [],
  };
}
const C1 = conversation("c1", "Prečo agent stojí?");
const C2 = conversation("c2", "Čo je v UAT?");

function BrowserBack() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(-1)}>
      prehliadač: späť
    </button>
  );
}

function renderApp(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Sidebar />
      <BrowserBack />
      <Routes>
        <Route path="/poradca" element={<PoradcaPage />} />
        <Route path="/riadiace-centrum" element={<div>RIADIACE CENTRUM</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function menu(label: string) {
  fireEvent.click(screen.getByRole("button", { name: label }));
}

async function box(): Promise<HTMLTextAreaElement> {
  return (await screen.findByPlaceholderText(BOX)) as HTMLTextAreaElement;
}

async function goToRiadiaceCentrumAndBackViaMenu() {
  menu("Riadiace centrum");
  expect(await screen.findByText("RIADIACE CENTRUM")).toBeInTheDocument();
  menu("Poradca");
}

beforeEach(() => {
  Object.values(api).forEach((f) => "mockReset" in f && f.mockReset());
  api.buildPoradcaWsUrl.mockReturnValue("ws://test/ws");
  api.getPoradcaStatusApi.mockResolvedValue({ ready: true, problems: [], running: 0, max_concurrent: 3 });
  api.getPoradcaContextApi.mockResolvedValue({ project_id: "p1", project_slug: "demo", project_name: "Demo", versions: [] });
  api.listPoradcaConversationsApi.mockResolvedValue([C1, C2]);
  api.getPoradcaConversationApi.mockImplementation(async (id: string) => (id === "c2" ? C2 : C1));
  ctx.state.selectedProject = { slug: "demo", name: "Demo" };
  window.localStorage.clear();
});

describe("DEV-24 — the question for Poradca survives a trip to Riadiace centrum", () => {
  it("in an open conversation: back through the sidebar, the conversation and the question are there", async () => {
    renderApp("/poradca?c=c1");
    fireEvent.change(await box(), { target: { value: "Prečo agent čaká na mňa?" } });
    await goToRiadiaceCentrumAndBackViaMenu();
    await waitFor(async () => expect(await box()).toHaveValue("Prečo agent čaká na mňa?"));
    expect(await screen.findByText(RESTORED)).toBeInTheDocument();
    expect(api.getPoradcaConversationApi).toHaveBeenLastCalledWith("c1");
  });

  it("in an open conversation: back through the browser's Back, the question is there", async () => {
    renderApp("/poradca?c=c1");
    fireEvent.change(await box(), { target: { value: "Prečo agent čaká na mňa?" } });
    menu("Riadiace centrum");
    expect(await screen.findByText("RIADIACE CENTRUM")).toBeInTheDocument();
    menu("prehliadač: späť");
    await waitFor(async () => expect(await box()).toHaveValue("Prečo agent čaká na mňa?"));
  });

  it("a new conversation not yet sent: back through the sidebar, the question is there", async () => {
    renderApp("/poradca");
    fireEvent.change(await box(), { target: { value: "Aké verzie má projekt?" } });
    await goToRiadiaceCentrumAndBackViaMenu();
    await waitFor(async () => expect(await box()).toHaveValue("Aké verzie má projekt?"));
    expect(api.getPoradcaConversationApi).not.toHaveBeenCalled();
  });

  it("„Nový rozhovor“ is remembered too: back through the sidebar he is in the new one, not the old", async () => {
    renderApp("/poradca?c=c1");
    await box();
    menu("Nový rozhovor");
    fireEvent.change(await box(), { target: { value: "Nová otázka" } });
    await goToRiadiaceCentrumAndBackViaMenu();
    await waitFor(async () => expect(await box()).toHaveValue("Nová otázka"));
    expect(api.getPoradcaConversationApi).toHaveBeenCalledTimes(1);
  });

  it("a question half-written in one conversation does not show up in another", async () => {
    renderApp("/poradca?c=c1");
    fireEvent.change(await box(), { target: { value: "otázka k prvému" } });
    fireEvent.click(await screen.findByText("Čo je v UAT?"));
    await waitFor(() => expect(api.getPoradcaConversationApi).toHaveBeenLastCalledWith("c2"));
    await waitFor(async () => expect(await box()).toHaveValue(""));
    fireEvent.click(screen.getByText("Prečo agent stojí?"));
    await waitFor(async () => expect(await box()).toHaveValue("otázka k prvému"));
  });

  it("a sent question is gone for good; a failed one stays", async () => {
    api.askPoradcaApi.mockRejectedValueOnce(new Error("sieť"));
    api.askPoradcaApi.mockResolvedValue(C1);
    renderApp("/poradca?c=c1");
    fireEvent.change(await box(), { target: { value: "Prečo?" } });
    fireEvent.click(screen.getByRole("button", { name: /Opýtať sa/ }));
    await waitFor(() => expect(api.askPoradcaApi).toHaveBeenCalledTimes(1));
    await goToRiadiaceCentrumAndBackViaMenu();
    await waitFor(async () => expect(await box()).toHaveValue("Prečo?"));

    fireEvent.click(screen.getByRole("button", { name: /Opýtať sa/ }));
    await waitFor(() => expect(api.askPoradcaApi).toHaveBeenCalledTimes(2));
    await waitFor(async () => expect(await box()).toHaveValue(""));
    await goToRiadiaceCentrumAndBackViaMenu();
    await waitFor(async () => expect(await box()).toHaveValue(""));
    expect(screen.queryByText(RESTORED)).not.toBeInTheDocument();
  });

  it("a remembered conversation that no longer exists is not reopened", async () => {
    window.localStorage.setItem("nex.poradca.open.demo", "zmazany");
    renderApp("/poradca");
    await box();
    // The list has to arrive before the memory can be checked against it — then the stale memory is dropped.
    expect(await screen.findByText("Prečo agent stojí?")).toBeInTheDocument();
    await waitFor(() => expect(window.localStorage.getItem("nex.poradca.open.demo")).toBeNull());
    expect(api.getPoradcaConversationApi).not.toHaveBeenCalled();
  });
});

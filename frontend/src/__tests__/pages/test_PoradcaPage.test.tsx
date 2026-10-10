/**
 * Poradca (ICCINT-167): obrazovka rozhovorov. Rozhranie je atrapa; WebSocket je nahradený tichým stubom.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  saveRequestToBacklogApi: vi.fn(),
  buildPoradcaWsUrl: vi.fn(() => "ws://test/ws"),
  getPoradcaAttachmentApi: vi.fn(),
}));
vi.mock("@/services/api/poradca", () => api);

const ctx = vi.hoisted(() => ({
  state: {
    selectedProject: { slug: "demo", name: "Demo" } as { slug: string; name: string } | null,
    selectedVersion: null as { versionId: string; versionNumber: string } | null,
    setSelectedVersion: vi.fn(),
  },
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) => sel(ctx.state),
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ token: "t", user: { id: "me", username: "tibor" } }),
}));

class SilentSocket {
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  close() {}
}
vi.stubGlobal("WebSocket", SilentSocket);

import PoradcaPage from "@/pages/PoradcaPage";

const V_RUNNING = {
  id: "v13",
  version_number: "1.3.0",
  stage: "programovanie",
  status: "agent_working",
  instruction_open: true,
  instruction_closed_reason: null,
};
const V_DONE = {
  id: "v11",
  version_number: "1.1.0",
  stage: "done",
  status: "done",
  instruction_open: false,
  instruction_closed_reason: "Verzia je hotová — zmena patrí do novej verzie.",
};

function conversation(versionId: string | null, messages: unknown[]) {
  return {
    id: "c1",
    project_id: "p1",
    version_id: versionId,
    version_number: versionId === "v13" ? "1.3.0" : versionId === "v11" ? "1.1.0" : null,
    author_id: "me",
    author_name: "Tibor",
    title: "Prečo agent stojí?",
    running: false,
    created_at: "2026-10-05T10:00:00Z",
    updated_at: "2026-10-05T10:00:00Z",
    messages,
  };
}

const ANSWER_WITH_INSTRUCTION = {
  id: "m2",
  author: "poradca",
  content: "Agent stojí na teste.\n<pokyn-pre-agenta>Oprav test_login.</pokyn-pre-agenta>",
  steps: [{ tool: "zaznam_agenta", target: "posledných 80 krokov" }],
  status: "done",
  model: "claude-opus-5-5",
  input_tokens: 1000,
  output_tokens: 100,
  cost: 0.42,
  duration_seconds: 80,
  error: null,
  created_at: "2026-10-05T10:00:01Z",
  finished_at: "2026-10-05T10:01:21Z",
  instruction: "Oprav test_login.",
  backlog_request: null,
  captured_backlog_number: null,
};

function renderPage(url = "/poradca?c=c1") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/poradca" element={<PoradcaPage />} />
        <Route path="/riadiace-centrum" element={<div>RIADIACE CENTRUM</div>} />
        <Route path="/projects" element={<div>PROJEKTY</div>} />
        <Route path="/projects/:slug/backlog" element={<div>ZÁSOBNÍK</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  Object.values(api).forEach((f) => "mockReset" in f && f.mockReset());
  api.buildPoradcaWsUrl.mockReturnValue("ws://test/ws");
  api.getPoradcaStatusApi.mockResolvedValue({ ready: true, problems: [], running: 0, max_concurrent: 3 });
  api.getPoradcaContextApi.mockResolvedValue({
    project_id: "p1",
    project_slug: "demo",
    project_name: "Demo",
    versions: [V_RUNNING, V_DONE],
  });
  api.listPoradcaConversationsApi.mockResolvedValue([conversation("v13", [])]);
  ctx.state.selectedProject = { slug: "demo", name: "Demo" };
  ctx.state.setSelectedVersion = vi.fn();
  window.localStorage.clear();
});

describe("PoradcaPage", () => {
  it("without a pinned project offers the way to Projekty", () => {
    ctx.state.selectedProject = null;
    renderPage("/poradca");
    expect(screen.getByText("Nemáš vybraný projekt")).toBeInTheDocument();
  });

  it("shows the scope with build phase and status, and the conversation list", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", []));
    renderPage();
    const select = (await screen.findByLabelText("O čom sa rozprávame")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("v13"));
    expect(screen.getByRole("option", { name: "1.3.0 — programovanie, agent pracuje" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "1.1.0 — hotová" })).toBeInTheDocument();
    expect(screen.getByText("Prečo agent stojí?")).toBeInTheDocument();
  });

  it("a running answer shows what Poradca is doing and can be stopped", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(
      conversation("v13", [
        { ...ANSWER_WITH_INSTRUCTION, status: "running", content: "", instruction: null, model: null, cost: null },
      ]),
    );
    api.stopPoradcaApi.mockResolvedValue({ stopping: true });
    renderPage();
    expect(await screen.findByText("Rozoberám kroky agenta stavby — posledných 80 krokov")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Opýtaj sa Poradcu/)).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Zastaviť/ }));
    await waitFor(() => expect(api.stopPoradcaApi).toHaveBeenCalledWith("m2"));
  });

  // DEV-22: the instruction is handed off as PENDING — which box of Riadiace centrum takes it is decided there
  // (test_PoradcaHandoff_RiadiaceCentrum walks that path through the real page). Here: handed off, not sent,
  // and the Manažér's own draft is left alone.
  it("hands the instruction off to Riadiace centrum — never sends it", async () => {
    window.localStorage.setItem("nex.draft.rozhovor.v13", "moja rozpísaná veta");
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", [ANSWER_WITH_INSTRUCTION]));
    renderPage();
    expect(await screen.findByText("trvalo 1 min 20 s")).toBeInTheDocument();
    expect(screen.getByText(/0,42\s€ · Opus 5\.5/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Vložiť do Riadiaceho centra/ }));
    expect(await screen.findByText("RIADIACE CENTRUM")).toBeInTheDocument();
    expect(window.localStorage.getItem("nex.poradca.pending.v13")).toBe("Oprav test_login.");
    expect(window.localStorage.getItem("nex.draft.rozhovor.v13")).toBe("moja rozpísaná veta");
    expect(ctx.state.setSelectedVersion).toHaveBeenCalledWith({ versionId: "v13", versionNumber: "1.3.0" });
  });

  it("greys the insert out with the reason when the build cannot take it", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v11", [ANSWER_WITH_INSTRUCTION]));
    renderPage();
    const button = await screen.findByRole("button", { name: /Vložiť do Riadiaceho centra/ });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "Verzia je hotová — zmena patrí do novej verzie.");
  });

  it("a new conversation is asked with the chosen scope", async () => {
    api.createPoradcaConversationApi.mockResolvedValue(conversation(null, []));
    renderPage("/poradca?verzia=v13");
    const select = (await screen.findByLabelText("O čom sa rozprávame")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("v13"));
    fireEvent.change(screen.getByPlaceholderText(/Opýtaj sa Poradcu/), { target: { value: "Čo je v UAT?" } });
    fireEvent.click(screen.getByRole("button", { name: /Opýtať sa/ }));
    await waitFor(() => expect(api.createPoradcaConversationApi).toHaveBeenCalledWith("demo", "Čo je v UAT?", "v13", []));
  });

  it("says why it cannot run instead of accepting a question", async () => {
    api.getPoradcaStatusApi.mockResolvedValue({ ready: false, problems: ["chýba obraz"], running: 0, max_concurrent: 3 });
    renderPage("/poradca");
    expect(await screen.findByText("Poradca teraz nevie bežať: chýba obraz")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Opýtaj sa Poradcu/)).toBeDisabled();
  });

  // Premenovanie a vymazanie (Director 05.10.2026: „chýba mi premenovanie rozhovoru a vymazanie rozhovoru").

  it("renames in place: Enter saves one clean line and the list shows it", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", []));
    let finish: (v: unknown) => void = () => {};
    api.renamePoradcaConversationApi.mockReturnValue(new Promise((r) => (finish = r)));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Premenovať rozhovor" }));
    const input = screen.getByLabelText("Názov rozhovoru") as HTMLInputElement;
    expect(input.value).toBe("Prečo agent stojí?");
    fireEvent.change(input, { target: { value: "  Nový \n  názov " } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    // Kým sa ukladá, pole zašedne a prehliadač z neho odíde — to je druhá cesta k uloženiu; uloží sa raz.
    fireEvent.blur(input);
    finish({ ...conversation("v13", []), title: "Nový názov" });
    expect(await screen.findByText("Nový názov")).toBeInTheDocument();
    expect(api.renamePoradcaConversationApi).toHaveBeenCalledTimes(1);
    expect(api.renamePoradcaConversationApi).toHaveBeenCalledWith("c1", "Nový názov");
    expect(screen.queryByLabelText("Názov rozhovoru")).not.toBeInTheDocument();
  });

  it("Esc cancels the rename and nothing is saved", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", []));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Premenovať rozhovor" }));
    const input = screen.getByLabelText("Názov rozhovoru");
    fireEvent.change(input, { target: { value: "Iný" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByLabelText("Názov rozhovoru")).not.toBeInTheDocument();
    expect(screen.getByText("Prečo agent stojí?")).toBeInTheDocument();
    expect(api.renamePoradcaConversationApi).not.toHaveBeenCalled();
  });

  it("leaving the field saves; a failed save keeps the typed title open and says why", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", []));
    api.renamePoradcaConversationApi.mockRejectedValue(new Error("network"));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Premenovať rozhovor" }));
    const input = screen.getByLabelText("Názov rozhovoru") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Rozpísaný názov" } });
    fireEvent.blur(input);
    await waitFor(() => expect(api.renamePoradcaConversationApi).toHaveBeenCalledWith("c1", "Rozpísaný názov"));
    expect(await screen.findByText(/Premenovanie zlyhalo/)).toBeInTheDocument();
    expect((screen.getByLabelText("Názov rozhovoru") as HTMLInputElement).value).toBe("Rozpísaný názov");
    // Druhý pokus prejde.
    api.renamePoradcaConversationApi.mockResolvedValue({ ...conversation("v13", []), title: "Rozpísaný názov" });
    fireEvent.submit(screen.getByLabelText("Názov rozhovoru").closest("form") as HTMLFormElement);
    await waitFor(() => expect(screen.queryByLabelText("Názov rozhovoru")).not.toBeInTheDocument());
    expect(api.renamePoradcaConversationApi).toHaveBeenCalledTimes(2);
  });

  it("delete asks first, says the text is gone for good and the cost stays, then removes it", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", []));
    api.deletePoradcaConversationApi.mockResolvedValue(undefined);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Vymazať rozhovor" }));
    expect(api.deletePoradcaConversationApi).not.toHaveBeenCalled();
    expect(screen.getByText("Vymazať „Prečo agent stojí?“ natrvalo?")).toBeInTheDocument();
    expect(screen.getByText("Text sa nedá obnoviť; cena ostane v Nákladoch.")).toBeInTheDocument();
    api.listPoradcaConversationsApi.mockResolvedValue([]);
    fireEvent.click(screen.getByRole("button", { name: "Vymazať" }));
    await waitFor(() => expect(api.deletePoradcaConversationApi).toHaveBeenCalledWith("c1"));
    // Bol otvorený → stránka sa vráti na nový rozhovor; v zozname už nie je.
    expect(await screen.findByText(/Opýtaj sa na čokoľvek k projektu/)).toBeInTheDocument();
    expect(screen.queryByText("Prečo agent stojí?")).not.toBeInTheDocument();
  });

  it("Ponechať keeps the conversation and calls nothing", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", []));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Vymazať rozhovor" }));
    fireEvent.click(screen.getByRole("button", { name: "Ponechať" }));
    expect(screen.getByText("Prečo agent stojí?")).toBeInTheDocument();
    expect(api.deletePoradcaConversationApi).not.toHaveBeenCalled();
  });

  it("while Poradca answers the bin is greyed out with the way forward", async () => {
    api.listPoradcaConversationsApi.mockResolvedValue([{ ...conversation("v13", []), running: true }]);
    renderPage("/poradca");
    const bin = await screen.findByRole("button", { name: "Vymazať rozhovor" });
    await waitFor(() => expect(bin).toBeDisabled());
    expect(bin).toHaveAttribute(
      "title",
      "Kým Poradca odpovedá, rozhovor sa nedá vymazať — najprv odpoveď zastav.",
    );
    // Premenovať sa dá aj počas odpovede.
    expect(screen.getByRole("button", { name: "Premenovať rozhovor" })).toBeEnabled();
  });

  it("an answer that finished while I was elsewhere frees the bin when I open the conversation", async () => {
    // Zoznam ešte hovorí „beží" (odpoveď dobehla, kým som bol v inom rozhovore); otvorený rozhovor vie lepšie.
    api.listPoradcaConversationsApi.mockResolvedValue([{ ...conversation("v13", []), running: true }]);
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", [ANSWER_WITH_INSTRUCTION]));
    renderPage();
    expect(await screen.findByText("trvalo 1 min 20 s")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Vymazať rozhovor" })).toBeEnabled());
    expect(screen.queryByTitle("Poradca odpovedá")).not.toBeInTheDocument();
  });

  it("a later success clears an old failure note", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", []));
    api.deletePoradcaConversationApi.mockRejectedValueOnce(new Error("network"));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Vymazať rozhovor" }));
    fireEvent.click(screen.getByRole("button", { name: "Vymazať" }));
    expect(await screen.findByText(/Vymazanie zlyhalo/)).toBeInTheDocument();
    api.renamePoradcaConversationApi.mockResolvedValue({ ...conversation("v13", []), title: "Nový" });
    fireEvent.click(screen.getByRole("button", { name: "Premenovať rozhovor" }));
    const input = screen.getByLabelText("Názov rozhovoru");
    fireEvent.change(input, { target: { value: "Nový" } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    await waitFor(() => expect(screen.queryByText(/Vymazanie zlyhalo/)).not.toBeInTheDocument());
  });

  it("the open conversation's live answer greys the bin too, even before the list catches up", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(
      conversation("v13", [
        { ...ANSWER_WITH_INSTRUCTION, status: "running", content: "", instruction: null, model: null, cost: null },
      ]),
    );
    renderPage();
    await screen.findByText("Rozoberám kroky agenta stavby — posledných 80 krokov");
    // Kôš zašedne až po zosúladení zoznamu s otvoreným rozhovorom (efekt po vykreslení) — bez čakania
    // skúška padala v polovici behov (CI 05.10.2026).
    await waitFor(() => expect(screen.getByRole("button", { name: "Vymazať rozhovor" })).toBeDisabled());
  });

  // DEV-29 — Director 08.10.2026: „O verziách rozhodujem ja. Treba, aby zapísal len do zásobníku."
  describe("a request from Poradca goes to the Zásobník only", () => {
    const REQUEST_ANSWER = {
      ...ANSWER_WITH_INSTRUCTION,
      content: "Do tejto stavby to nepatrí.\n<poziadavka-do-zasobnika>Riadne spracovanie dobropisov.</poziadavka-do-zasobnika>",
      instruction: null,
      backlog_request: "Riadne spracovanie dobropisov.",
    };

    it("„Uložiť do Zásobníka“ saves it, says under which number, and opens no version", async () => {
      api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", [REQUEST_ANSWER]));
      api.saveRequestToBacklogApi.mockResolvedValue({ backlog_item_id: "b7", number: 7, project_slug: "demo", created: true });
      renderPage();
      fireEvent.click(await screen.findByRole("button", { name: /Uložiť do Zásobníka/ }));
      await waitFor(() => expect(api.saveRequestToBacklogApi).toHaveBeenCalledWith("m2"));
      expect(await screen.findByText("Uložené v Zásobníku ako REQ-7.")).toBeInTheDocument();
      expect(ctx.state.setSelectedVersion).not.toHaveBeenCalled();
      expect(screen.queryByRole("button", { name: /Uložiť do Zásobníka/ })).not.toBeInTheDocument();
      expect(screen.queryByText(/novú verziu|založenú verziu/)).not.toBeInTheDocument();
    });

    it("an answer already saved says so and offers the way to the Zásobník", async () => {
      api.getPoradcaConversationApi.mockResolvedValue(
        conversation("v13", [{ ...REQUEST_ANSWER, captured_backlog_number: 7 }]),
      );
      renderPage();
      expect(await screen.findByText("Uložené v Zásobníku ako REQ-7.")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Uložiť do Zásobníka/ })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Otvoriť Zásobník" }));
      expect(await screen.findByText("ZÁSOBNÍK")).toBeInTheDocument();
    });
  });
});

describe("PoradcaPage — snímka obrazovky v otázke (DEV-52)", () => {
  const LIMITS = {
    ready: true,
    problems: [],
    running: 0,
    max_concurrent: 3,
    attachment_max_bytes: 5 * 1024 * 1024,
    attachments_max_count: 5,
    attachments_max_total_bytes: 15 * 1024 * 1024,
    attachment_types: ["image/gif", "image/jpeg", "image/png", "image/webp"],
  };
  const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3]);
  const PNG_BASE64 = btoa(String.fromCharCode(...PNG));

  beforeEach(() => {
    api.getPoradcaStatusApi.mockResolvedValue(LIMITS);
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", []));
    URL.createObjectURL = vi.fn(() => "blob:nahled");
    URL.revokeObjectURL = vi.fn();
  });

  async function box() {
    return (await screen.findByPlaceholderText(/Opýtaj sa Poradcu/)) as HTMLTextAreaElement;
  }

  function paste(target: HTMLElement, files: File[], types: string[] = files.length ? ["Files"] : []) {
    fireEvent.paste(target, { clipboardData: { files, items: [], types, getData: () => "" } });
  }

  it("⚠️ snímka vložená zo schránky (Ctrl+V) ide s otázkou k Poradcovi", async () => {
    api.askPoradcaApi.mockResolvedValue(conversation("v13", []));
    renderPage();
    const textarea = await box();
    await waitFor(() => expect(screen.getByRole("button", { name: "Priložiť snímku obrazovky" })).toBeInTheDocument());

    paste(textarea, [new File([PNG], "image.png", { type: "image/png" })]);
    const preview = await screen.findByRole("img", { name: /^Snímka 1 .*\.png$/ });
    expect(preview).toHaveAttribute("src", "blob:nahled");

    fireEvent.change(textarea, { target: { value: "Čo je na snímke?" } });
    fireEvent.click(screen.getByRole("button", { name: /Opýtať sa/ }));

    await waitFor(() =>
      expect(api.askPoradcaApi).toHaveBeenCalledWith("c1", "Čo je na snímke?", [
        { name: preview.getAttribute("alt"), data: PNG_BASE64 },
      ]),
    );
    await waitFor(() => expect(screen.queryByTestId("pending-images")).not.toBeInTheDocument());
  });

  it("text vložený zo schránky ostane textom a nič sa nehlási", async () => {
    renderPage();
    const textarea = await box();
    await waitFor(() => expect(screen.getByRole("button", { name: "Priložiť snímku obrazovky" })).toBeInTheDocument());

    paste(textarea, [], ["text/plain"]);

    expect(screen.queryByTestId("pending-images")).not.toBeInTheDocument();
    expect(screen.queryByText(/schránka je prázdna/)).not.toBeInTheDocument();
  });

  it("⚠️ Ctrl+V so snímkou funguje kdekoľvek na stránke, nielen v poli — a len raz", async () => {
    renderPage();
    const textarea = await box();
    await waitFor(() => expect(screen.getByRole("button", { name: "Priložiť snímku obrazovky" })).toBeInTheDocument());

    paste(document.body, [new File([PNG], "mimo-pola.png", { type: "image/png" })]);
    paste(textarea, [new File([PNG], "v-poli.png", { type: "image/png" })]);

    expect(await screen.findByRole("img", { name: "mimo-pola.png" })).toBeInTheDocument();
    expect(screen.getAllByRole("img", { name: "v-poli.png" })).toHaveLength(1);
  });

  it("⚠️ po odchode zo stránky Poradcu vloženie obrázka inde v kokpite nezachytáva", async () => {
    const { unmount } = renderPage();
    await waitFor(() => expect(screen.getByRole("button", { name: "Priložiť snímku obrazovky" })).toBeInTheDocument());

    unmount();
    const notPrevented = fireEvent.paste(document.body, {
      clipboardData: { files: [new File([PNG], "inde.png", { type: "image/png" })], items: [], types: ["Files"] },
    });

    expect(notPrevented).toBe(true);
  });

  it("⚠️ Ctrl+V s prázdnou schránkou povie prečo, namiesto ticha", async () => {
    renderPage();
    const textarea = await box();
    await waitFor(() => expect(screen.getByRole("button", { name: "Priložiť snímku obrazovky" })).toBeInTheDocument());

    paste(textarea, [], []);

    expect(
      await screen.findByText(
        "Ctrl+V nedonieslo obrázok ani text — schránka je prázdna. Snímku urob na počítači, kde beží kokpit (vo " +
          "vzdialenej ploche priamo v nej, napríklad klávesom PrtScn), alebo ju vyber tlačidlom s obrázkom.",
      ),
    ).toBeInTheDocument();
  });

  it("pole otázky povie, že Ctrl+V vloží snímku", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByPlaceholderText(/Ctrl\+V vloží snímku obrazovky/)).toBeInTheDocument(),
    );
  });

  it("⚠️ iný súbor než obrázok ani priveľký obrázok neprijme a povie prečo", async () => {
    renderPage();
    const textarea = await box();
    await waitFor(() => expect(screen.getByRole("button", { name: "Priložiť snímku obrazovky" })).toBeInTheDocument());

    paste(textarea, [new File(["%PDF"], "faktura.pdf", { type: "image/svg+xml" })]);
    expect(await screen.findByText("„faktura.pdf“ nie je PNG, JPEG, WebP ani GIF.")).toBeInTheDocument();

    paste(textarea, [new File([new Uint8Array(6 * 1024 * 1024)], "velka.png", { type: "image/png" })]);
    expect(await screen.findByText("Obrázok má 6 MB — jeden môže mať najviac 5 MB.")).toBeInTheDocument();
    expect(screen.queryByTestId("pending-images")).not.toBeInTheDocument();
  });

  it("snímku prijme aj pretiahnutím a cez tlačidlo Priložiť", async () => {
    renderPage();
    const textarea = await box();
    await waitFor(() => expect(screen.getByRole("button", { name: "Priložiť snímku obrazovky" })).toBeInTheDocument());

    fireEvent.drop(textarea.closest("form")!, {
      dataTransfer: { files: [new File([PNG], "pretiahnuta.png", { type: "image/png" })], items: [] },
    });
    fireEvent.change(screen.getByTestId("poradca-image-picker"), {
      target: { files: [new File([PNG], "vybrana.png", { type: "image/png" })] },
    });

    expect(await screen.findByRole("img", { name: "pretiahnuta.png" })).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "vybrana.png" })).toBeInTheDocument();
  });

  it("priloženú snímku sa dá pred odoslaním odobrať", async () => {
    renderPage();
    const textarea = await box();
    await waitFor(() => expect(screen.getByRole("button", { name: "Priložiť snímku obrazovky" })).toBeInTheDocument());
    paste(textarea, [new File([PNG], "obrazovka.png", { type: "image/png" })]);

    fireEvent.click(await screen.findByRole("button", { name: "Odobrať obrazovka.png" }));

    expect(screen.queryByTestId("pending-images")).not.toBeInTheDocument();
  });

  it("otázka so snímkou ukáže náhľad a po kliknutí celý obrázok", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(
      conversation("v13", [
        {
          ...ANSWER_WITH_INSTRUCTION,
          id: "m1",
          author: "human",
          content: "Čo je na snímke?",
          instruction: null,
          attachments: [{ id: "a1b2", name: "Snímka 1.png", mime: "image/png", size_bytes: 11 }],
        },
      ]),
    );
    api.getPoradcaAttachmentApi.mockResolvedValue(new Blob([PNG], { type: "image/png" }));
    renderPage();

    fireEvent.click(await screen.findByRole("img", { name: "Snímka 1.png" }));

    expect(api.getPoradcaAttachmentApi).toHaveBeenCalledWith("c1", "a1b2");
    expect(screen.getByRole("dialog", { name: "Snímka 1.png" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Snímka 1.png" })).not.toBeInTheDocument());
  });
});

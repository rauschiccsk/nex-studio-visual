/**
 * DEV-36 — what to check in the Vizuál, before the link, ticked off with a count at „Schváliť vizuál".
 *
 * Director 09.10.2026, approving the Vizuál of NEX Inbox 1.7.0: „Po vyhotovení vizuálu pred tým linkom na
 * samotný vizual pomohlo by mi krátky popis čo všetko treba prekontrolovať vo vizuáli." With it, all of the
 * proposed extensions: a direct link to each screen, what the Vizuál cannot show, a list after every change,
 * and ticking with „skontrolované X z N" by the approve button (which stays clickable — he decides).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

import { ConversationThread } from "@/components/riadiace/ConversationThread";
import SchvalitBar from "@/components/riadiace/SchvalitBar";
import { useVizualChecksStore } from "@/store/vizualChecksStore";
import type { PipelineBoard, PipelineMessage, VizualChecklist } from "@/services/api/pipeline";

const URL = "https://vizual-nex-inbox.isnex.eu";

const FIRST: VizualChecklist = {
  round: "first",
  items: [
    {
      screen: "Prehľad",
      action: "Pozri kartu „Čaká na doručenie do Genesisu“.",
      expected: "Karta ukazuje 2 faktúry.",
      url: `${URL}/`,
    },
    {
      screen: "Detail faktúry INB-I-000114",
      action: "Otvor faktúru, ktorá čaká.",
      expected: "Pokojný modrý pruh.",
      url: `${URL}/invoices/INB-I-000114`,
    },
    { screen: "E-mailový náhľad", action: "Pozri text.", expected: "Text sedí.", url: null },
  ],
  not_verifiable: ["E-mail o výpadku príde až s naprogramovaným doručovateľom."],
};
const CHANGE: VizualChecklist = {
  round: "change",
  items: [{ screen: "Prehľad", action: "Pozri súčet.", expected: "Súčet je väčší.", url: `${URL}/` }],
  not_verifiable: [],
};

function note(seq: number, content: string, payload: Record<string, unknown>): PipelineMessage {
  return {
    id: `m${seq}`,
    version_id: "v-1",
    stage: "vizual",
    author: "system",
    recipient: "manazer",
    kind: "notification",
    content,
    status: "delivered",
    payload,
    created_at: "2026-10-09T08:00:00Z",
    seq,
  };
}

const LINK_MSG = note(10, `Vizuál je pripravený — otvor si ho: ${URL}`, {
  phase: "vizual",
  vizual_url: URL,
  vizual_checklist: FIRST,
});
const CHANGE_MSG = note(14, "Zmena je vo Vizuáli — čo po nej skontrolovať:", {
  phase: "vizual",
  vizual_checklist: CHANGE,
});

function board(stage = "vizual"): PipelineBoard {
  return {
    state: { current_stage: stage },
    recent_messages: [LINK_MSG, CHANGE_MSG],
    available_actions: ["schvalit"],
    vizual_checklists: [
      { ...FIRST, seq: 10 },
      { ...CHANGE, seq: 14 },
    ],
  } as unknown as PipelineBoard;
}

function renderCockpit(stage = "vizual") {
  return render(
    <>
      <ConversationThread versionId="v-1" messages={[LINK_MSG, CHANGE_MSG]} activity={[]} working={false} />
      <SchvalitBar board={board(stage)} versionId="v-1" onBoard={vi.fn()} />
    </>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  useVizualChecksStore.setState({ checked: {} });
});

describe("DEV-36 — the list of what to check in the Vizuál", () => {
  it("stands before the link, each screen with its own link, and says what the Vizuál cannot show", () => {
    renderCockpit();

    const list = screen.getByRole("region", { name: "Čo vo Vizuáli skontrolovať" });
    const link = screen.getByText(/Vizuál je pripravený — otvor si ho/);
    // DOM order: the list comes first, the link after it.
    expect(list.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const items = within(within(list).getAllByRole("list")[0]!).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[1]!).toHaveTextContent("Detail faktúry INB-I-000114");
    expect(items[1]!).toHaveTextContent("Otvor faktúru, ktorá čaká.");
    expect(items[1]!).toHaveTextContent("Pokojný modrý pruh.");
    const open = within(items[1]!).getByRole("link", { name: "Otvoriť obrazovku" });
    expect(open).toHaveAttribute("href", `${URL}/invoices/INB-I-000114`);
    expect(open).toHaveAttribute("target", "_blank");
    expect(within(items[2]!).queryByRole("link")).not.toBeInTheDocument();

    expect(within(list).getByText("Vo Vizuáli sa overiť nedá")).toBeInTheDocument();
    expect(within(list).getByText(/E-mail o výpadku príde až/)).toBeInTheDocument();
  });

  it("after a change: its own list, under the sentence that announces it", () => {
    renderCockpit();

    const list = screen.getByRole("region", { name: "Čo po zmene skontrolovať" });
    const sentence = screen.getByText("Zmena je vo Vizuáli — čo po nej skontrolovať:");
    expect(sentence.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(within(list).getAllByRole("list")[0]!).getAllByRole("listitem")).toHaveLength(1);
  });

  it("ticking counts by „Schváliť vizuál“, which stays clickable, and the ticks survive a reload", async () => {
    const { unmount } = renderCockpit();
    expect(screen.getByText("Skontrolované 0 z 4")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: "Skontrolované: Detail faktúry INB-I-000114" }));
    fireEvent.click(screen.getAllByRole("checkbox", { name: "Skontrolované: Prehľad" })[1]!);
    expect(screen.getByText("Skontrolované 2 z 4")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Schváliť vizuál" })).toBeEnabled();

    // A reload: the page's memory is gone, only what this browser stored comes back.
    unmount();
    const stored = window.localStorage.getItem("nex.vizual.checks");
    expect(stored).toContain("10:1");
    useVizualChecksStore.setState({ checked: {} }); // a fresh page starts empty …
    window.localStorage.setItem("nex.vizual.checks", stored!); // … with the browser's storage as it was
    await act(async () => {
      await useVizualChecksStore.persist.rehydrate();
    });
    renderCockpit();
    expect(screen.getByText("Skontrolované 2 z 4")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Skontrolované: Detail faktúry INB-I-000114" })).toBeChecked();

    fireEvent.click(screen.getByRole("checkbox", { name: "Skontrolované: Detail faktúry INB-I-000114" }));
    expect(screen.getByText("Skontrolované 1 z 4")).toBeInTheDocument();
  });

  it("counts only items that exist, and only at the Vizuál gate", () => {
    useVizualChecksStore.setState({ checked: { "v-1": ["10:0", "99:0"], "v-2": ["10:1"] } });
    const { unmount } = renderCockpit();
    expect(screen.getByText("Skontrolované 1 z 4")).toBeInTheDocument();
    unmount();

    renderCockpit("navrh");
    expect(screen.queryByText(/Skontrolované \d+ z \d+/)).not.toBeInTheDocument();
  });

  it("a message that says the list is missing shows just its text", () => {
    const missing = note(11, "Vizuál je pripravený — otvor si ho: x\n\nAI partner nedodal zoznam.", {
      vizual_url: URL,
      vizual_checklist_missing: true,
    });
    render(<ConversationThread versionId="v-1" messages={[missing]} activity={[]} working={false} />);
    expect(screen.getByText(/AI partner nedodal zoznam/)).toBeInTheDocument();
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});

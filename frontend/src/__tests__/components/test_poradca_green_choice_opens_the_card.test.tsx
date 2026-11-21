/**
 * DEV-427 — when Poradca has nothing to put into Riadiace centrum, the green choice of a card is a button: it opens
 * Riadiace centrum and the card has the option picked; nothing lands in the box and nothing is sent.
 *
 * Director 18.11.2026: „Keď poradca nemá čo pridať do Riadiaceho centra, ukazuje len volbu (zelený text). Chcel by som
 * aby v takom prípade tento zelený text bol ako tlačídlo, a keď kliknem na to presunie ma do Riadiaceho centra a vyberie
 * tú volbu čo je v texte. Toto chcem mať len vtedy ak Poradca nemá čo dodať, pretože ak má potom tam je už tlačidlo.“
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

const store = vi.hoisted(() => ({ setSelectedVersion: vi.fn() }));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({
      selectedProject: { slug: "nex-inbox" },
      selectedVersion: { versionId: "v333", versionNumber: "1.10.3" },
      setSelectedVersion: store.setSelectedVersion,
    }),
}));
vi.mock("@/services/api/poradca", () => ({
  stopPoradcaApi: vi.fn(),
  saveRequestToBacklogApi: vi.fn(),
  attachAgentTicketsApi: vi.fn(),
}));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

import DecisionCardsBar from "@/components/controlCenter/DecisionCardsBar";
import PoradcaAnswer from "@/components/poradca/PoradcaAnswer";
import { handOffChoice, takeHandedChoice, takeHandoff } from "@/lib/poradcaHandoff";
import type { PipelineBoard } from "@/services/api/pipeline";

const VERSION = "v333";
const INSTRUCTION = "Dedo v DEV-167 zmeral vykonávač — sieť príčinou nie je; hľadaj zdržanie v aplikácii.";
const GUIDE = "Usmerniť opravu pre AI Agenta";
const HOLD = "Zatiaľ podržať (rozhodnem neskôr)";

// ── Poradca's side: the button hands off the green choice with the instruction ─────────────────────────────────────

const BUILDING = {
  id: VERSION,
  version_number: "1.10.3",
  stage: "verifikacia",
  status: "blocked",
  instruction_open: true,
  instruction_closed_reason: null,
};

function answer(choices: unknown[], instruction: string | null = null, version: unknown = BUILDING) {
  const message = {
    id: "m-1",
    author: "poradca",
    content: `Na karte 1 vyber <volba karta="1">${GUIDE}</volba>.`,
    steps: [],
    status: "done",
    instruction,
    choices,
    ticket_proposal: null,
    plane_identifier: null,
    plane_url: null,
    agent_tickets: [],
  };
  return render(
    <MemoryRouter initialEntries={["/poradca"]}>
      <Routes>
        <Route
          path="/poradca"
          element={
            <PoradcaAnswer
              message={message as never}
              scopeVersion={version as never}
              projectVersions={[version] as never}
            />
          }
        />
        <Route path="/riadiace-centrum" element={<p>Riadiace centrum</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const greenButton = () => screen.queryByRole("button", { name: new RegExp(`karta 1:\\s*${GUIDE}`) });

afterEach(() => {
  window.localStorage.clear();
  store.setSelectedVersion.mockReset();
});

describe("the green choice is the way to Riadiace centrum when Poradca has no instruction (DEV-427)", () => {
  it("opens Riadiace centrum with the choice handed to the card and nothing in the box", () => {
    answer([{ card: 1, label: GUIDE, status: "on_card" }]);

    fireEvent.click(greenButton() as HTMLElement);

    expect(screen.getByText("Riadiace centrum")).toBeInTheDocument();
    expect(store.setSelectedVersion).toHaveBeenCalledWith({ versionId: VERSION, versionNumber: "1.10.3" });
    expect(takeHandoff(VERSION)).toBeNull();
    expect(takeHandedChoice(VERSION)).toEqual({ card: 1, label: GUIDE });
  });

  it("with an instruction the button „Vložiť do Riadiaceho centra“ carries it and the green choice stays text", () => {
    answer([{ card: 1, label: GUIDE, status: "on_card" }], INSTRUCTION);

    expect(screen.getByRole("button", { name: /Vložiť do Riadiaceho centra/ })).toBeInTheDocument();
    expect(greenButton()).toBeNull();
  });

  it("a choice the card does not offer stays text with its warning", () => {
    answer([{ card: 1, label: GUIDE, status: "not_on_card" }]);

    expect(greenButton()).toBeNull();
  });

  it("is grey with the reason when no version takes it now", () => {
    answer([{ card: 1, label: GUIDE, status: "on_card" }], null, {
      ...BUILDING,
      instruction_open: false,
      instruction_closed_reason: "Pole Riadiaceho centra teraz pokyn neprijme.",
    });

    expect(greenButton()).toBeDisabled();
    expect(greenButton()).toHaveAttribute("title", "Pole Riadiaceho centra teraz pokyn neprijme.");
  });
});

// ── the card's side: a choice handed alone is picked, the box stays empty ───────────────────────────────────

const CARD = {
  id: "m1",
  version_id: VERSION,
  stage: "verifikacia",
  author: "system",
  recipient: "manazer",
  kind: "consultation",
  content: "Auditor po 5 kolách stále FAIL — potrebné je tvoje rozhodnutie.",
  status: "sent",
  seq: 10,
  created_at: "2026-11-09T01:27:10Z",
  payload: {
    consultation: {
      id: "verifikacia-fail-x",
      source: "verifikacia_fail",
      decisions: [
        {
          key: "verifikacia_fail_next",
          question: "Auditor nevie verziu dostať cez koncovú Verifikáciu. Ako chceš pokračovať?",
          options: [
            { id: "guide_fix", label: GUIDE, recommended: true },
            { id: "hold", label: HOLD },
          ],
        },
        {
          key: "dalsia",
          question: "Druhá otázka",
          options: [{ id: "a", label: GUIDE }],
        },
      ],
    },
  },
};

function bar() {
  const board = {
    available_actions: ["decide"],
    state: { status: "blocked", block_reason: "decision_needed", current_stage: "verifikacia" },
    recent_messages: [CARD],
  } as unknown as PipelineBoard;
  return render(
    <MemoryRouter>
      <DecisionCardsBar board={board} versionId={VERSION} onBoard={vi.fn()} />
    </MemoryRouter>,
  );
}

const option = (name: string | RegExp) => screen.getByRole("button", { name });

describe("the card picks a choice handed off alone (DEV-427)", () => {
  it("handed off before the card is shown", () => {
    handOffChoice(VERSION, { card: 1, label: HOLD });

    bar();

    expect(option(new RegExp(`^${HOLD.replace(/[()]/g, "\\$&")}`))).toHaveAttribute("aria-pressed", "true");
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("");
  });

  it("handed off while the card is open", () => {
    bar();

    act(() => handOffChoice(VERSION, { card: 1, label: GUIDE }));

    expect(option(new RegExp(`^${GUIDE}`))).toHaveAttribute("aria-pressed", "true");
  });
});

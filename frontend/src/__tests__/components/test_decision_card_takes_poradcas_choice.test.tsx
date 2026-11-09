/**
 * DEV-333 — „Vložiť do Riadiaceho centra“ hands off the choice Poradca marked green too, and the card has it picked.
 *
 * 09.11.2026 on the card „Auditor po 5 kolách stále FAIL“ of NEX Inbox 1.10.3 Poradca recommended, in green, „Usmerniť
 * opravu pre AI Agenta“; the button put its instruction under the card, but the Director still had to pick the option
 * himself. Director: „ak v Poradcu ja zadám presunúť niečo do riadiaceho centra, tak chcel by som aby hneď nastavil aj
 * voľbu, ktorú na začiatku píše zelenou farbou.“ Only a green choice travels (the card offers it word for word), only
 * to the card it names, never over what the Manažér picked himself — and nothing is sent: „Rozhodnúť“ stays his.
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
import { handOffInstruction, takeHandedChoice, type HandedChoice } from "@/lib/poradcaHandoff";
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

function answer(choices: unknown[]) {
  const message = {
    id: "m-1",
    author: "poradca",
    content: `Na karte 1 vyber <volba karta="1">${GUIDE}</volba>.`,
    steps: [],
    status: "done",
    instruction: INSTRUCTION,
    backlog_request: null,
    captured_backlog_number: null,
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
              scopeVersion={BUILDING as never}
              projectVersions={[BUILDING] as never}
            />
          }
        />
        <Route path="/riadiace-centrum" element={<p>Riadiace centrum</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const insertButton = () => screen.getByRole("button", { name: /Vložiť do Riadiaceho centra/ });

afterEach(() => {
  window.localStorage.clear();
  store.setSelectedVersion.mockReset();
});

describe("Vložiť do Riadiaceho centra hands off the green choice (DEV-333)", () => {
  it("the green choice of a card goes with the instruction", () => {
    answer([{ card: 1, label: GUIDE, status: "on_card" }]);

    fireEvent.click(insertButton());

    expect(takeHandedChoice(VERSION)).toEqual({ card: 1, label: GUIDE });
  });

  it("an orange choice — one the card does not offer — does not go", () => {
    answer([{ card: 1, label: "Oprav to rýchlo", status: "not_on_card" }]);

    fireEvent.click(insertButton());

    expect(takeHandedChoice(VERSION)).toBeNull();
  });

  it("an answer for the agent's question is no card choice and does not go either", () => {
    answer([{ card: null, label: "Áno", status: "answer" }]);

    fireEvent.click(insertButton());

    expect(takeHandedChoice(VERSION)).toBeNull();
  });
});

// ── the card's side: the handed choice is picked ──────────────────────────────────────────────────────────────────

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
const own = () => option(/^Vlastné rozhodnutie/);
const note = () => screen.getByRole("textbox") as HTMLTextAreaElement;

function handOff(choice: HandedChoice | null) {
  handOffInstruction(VERSION, INSTRUCTION, choice);
}

describe("the card picks the choice Poradca handed off (DEV-333)", () => {
  it("handed off before the card is shown: the option is picked and the instruction stands under it", () => {
    handOff({ card: 1, label: HOLD });

    bar();

    expect(option(new RegExp(`^${HOLD.replace(/[()]/g, "\\$&")}`))).toHaveAttribute("aria-pressed", "true");
    expect(option(new RegExp(`^${GUIDE}`))).toHaveAttribute("aria-pressed", "false");
    expect(note().value).toContain(INSTRUCTION);
  });

  it("handed off while the card is open: the option is picked at once", () => {
    bar();

    act(() => handOff({ card: 1, label: GUIDE }));

    expect(option(new RegExp(`^${GUIDE}`))).toHaveAttribute("aria-pressed", "true");
  });

  it("the Manažér's own decision is a choice of every card", () => {
    handOff({ card: 1, label: "Vlastné rozhodnutie" });

    bar();

    expect(own()).toHaveAttribute("aria-pressed", "true");
  });

  it("a choice for another card picks nothing on this one", () => {
    handOff({ card: 2, label: GUIDE });

    bar();

    expect(option(new RegExp(`^${GUIDE}`))).toHaveAttribute("aria-pressed", "false");
    expect(own()).toHaveAttribute("aria-pressed", "false");
  });

  it("a choice the card does not offer picks nothing", () => {
    handOff({ card: 1, label: "Oprav to rýchlo" });

    bar();

    expect(option(new RegExp(`^${GUIDE}`))).toHaveAttribute("aria-pressed", "false");
    expect(own()).toHaveAttribute("aria-pressed", "false");
  });

  it("what the Manažér picked himself is never overwritten", () => {
    bar();
    fireEvent.click(option(new RegExp(`^${GUIDE}`)));

    act(() => handOff({ card: 1, label: HOLD }));

    expect(option(new RegExp(`^${GUIDE}`))).toHaveAttribute("aria-pressed", "true");
    expect(note().value).toContain(INSTRUCTION);
  });
});

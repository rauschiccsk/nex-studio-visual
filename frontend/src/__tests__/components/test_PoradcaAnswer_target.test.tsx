/**
 * DEV-294 — „Vložiť do Riadiaceho centra“ works as usual also in a conversation about a finished version.
 *
 * 03.11.2026 the Director asked Poradca about the task plan of NEX Manager 1.5.0 in the conversation „1.4.0 —
 * rozhodnutia v Programovaní“ (the Poradca page reopens the last conversation). Poradca prepared the instruction, but
 * the button stayed grey: 1.4.0 is finished. Director: „stlačím tlačidlo ako obvykle a upresnenie pôjde do
 * riadiaceho centra.“ The instruction goes to the build that runs now — the pinned version first.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import { instructionTarget, takeHandoff } from "@/lib/poradcaHandoff";

const store = vi.hoisted(() => ({ pinned: null as string | null, setSelectedVersion: vi.fn() }));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({
      selectedProject: { slug: "nex-manager" },
      selectedVersion: store.pinned ? { versionId: store.pinned, versionNumber: "?" } : null,
      setSelectedVersion: store.setSelectedVersion,
    }),
}));
vi.mock("@/services/api/poradca", () => ({
  stopPoradcaApi: vi.fn(),
  saveRequestToBacklogApi: vi.fn(),
  attachAgentTicketsApi: vi.fn(),
}));

import PoradcaAnswer from "@/components/poradca/PoradcaAnswer";

const DONE_140 = {
  id: "v-140",
  version_number: "1.4.0",
  stage: "done",
  status: "done",
  instruction_open: false,
  instruction_closed_reason: "Verzia je hotová — zmena patrí do novej verzie.",
};
const BUILDING_150 = {
  id: "v-150",
  version_number: "1.5.0",
  stage: "programovanie",
  status: "awaiting_manazer",
  instruction_open: true,
  instruction_closed_reason: null,
};
const BUILDING_160 = { ...BUILDING_150, id: "v-160", version_number: "1.6.0" };
const WAITING_FOR_COCKPIT = {
  ...BUILDING_150,
  id: "v-151",
  version_number: "1.5.1",
  status: "blocked",
  instruction_open: false,
  instruction_closed_reason: "Stavba čaká na opravu kokpitu — pole v Riadiacom centre je zatvorené.",
};

const INSTRUCTION = "Plán je dobrý, doplň do neho tri veci.";

function answer(scopeVersion: unknown, projectVersions: unknown[]) {
  const message = {
    id: "m-1",
    author: "poradca",
    content: "Navrhujem plán vrátiť.",
    steps: [],
    status: "done",
    instruction: INSTRUCTION,
    backlog_request: null,
    captured_backlog_number: null,
    choices: [],
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
              scopeVersion={scopeVersion as never}
              projectVersions={projectVersions as never}
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
  store.pinned = null;
  store.setSelectedVersion.mockReset();
  window.localStorage.clear();
});

describe("Vložiť do Riadiaceho centra in a conversation about a finished version (DEV-294)", () => {
  it("puts the instruction into the build that runs now, as usual", () => {
    store.pinned = "v-150";
    answer(DONE_140, [BUILDING_150, DONE_140]);

    expect(insertButton()).toBeEnabled();
    expect(insertButton()).toHaveAttribute(
      "title",
      "Vloží pokyn do poľa Riadiaceho centra verzie 1.5.0 — odošleš ho sám.",
    );
    fireEvent.click(insertButton());

    expect(takeHandoff("v-150")).toBe(INSTRUCTION);
    expect(takeHandoff("v-140")).toBeNull();
    expect(store.setSelectedVersion).toHaveBeenCalledWith({ versionId: "v-150", versionNumber: "1.5.0" });
    expect(screen.getByText("Riadiace centrum")).toBeInTheDocument();
  });

  it("a version that is being built keeps its own conversation's instruction", () => {
    store.pinned = "v-160";
    answer(BUILDING_150, [BUILDING_160, BUILDING_150]);
    fireEvent.click(insertButton());

    expect(takeHandoff("v-150")).toBe(INSTRUCTION);
  });
});

describe("where the instruction goes (instructionTarget)", () => {
  it("the conversation's own version while its field takes it", () => {
    expect(instructionTarget(BUILDING_150 as never, [BUILDING_160, BUILDING_150] as never, "v-160")).toEqual({
      version: BUILDING_150,
      blocked: null,
    });
  });

  it("a finished version or the whole project: the pinned version that takes it, otherwise the only one", () => {
    const versions = [BUILDING_160, BUILDING_150, DONE_140] as never;
    expect(instructionTarget(DONE_140 as never, versions, "v-150").version).toEqual(BUILDING_150);
    expect(instructionTarget(null, versions, "v-160").version).toEqual(BUILDING_160);
    expect(instructionTarget(DONE_140 as never, [BUILDING_150, DONE_140] as never, null).version).toEqual(BUILDING_150);
    expect(instructionTarget(DONE_140 as never, [BUILDING_150, DONE_140] as never, "v-140").version).toEqual(
      BUILDING_150,
    );
  });

  it("two builds and none pinned: it does not guess and says how to choose", () => {
    expect(instructionTarget(DONE_140 as never, [BUILDING_160, BUILDING_150, DONE_140] as never, null)).toEqual({
      version: null,
      blocked: "Stavia sa viac verzií — v Projektoch pripni tú, do ktorej pokyn patrí.",
    });
  });

  it("no build takes it: grey with the true reason", () => {
    expect(instructionTarget(DONE_140 as never, [DONE_140] as never, null)).toEqual({
      version: null,
      blocked: "Verzia je hotová — zmena patrí do novej verzie.",
    });
    expect(instructionTarget(null, [DONE_140] as never, null)).toEqual({
      version: null,
      blocked: "Žiadna verzia projektu teraz pokyn neprijme — stavba nebeží.",
    });
  });

  it("a build that waits for the cockpit is not bypassed into another version", () => {
    expect(instructionTarget(WAITING_FOR_COCKPIT as never, [BUILDING_160, WAITING_FOR_COCKPIT] as never, "v-160")).toEqual(
      { version: null, blocked: "Stavba čaká na opravu kokpitu — pole v Riadiacom centre je zatvorené." },
    );
  });
});

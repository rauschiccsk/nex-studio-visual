/**
 * DEV-218 — the tickets the agent asked for: one click under Poradca's answer has the cockpit read them from Plane,
 * attach them as files and put the lines that say where into the answer for the agent — the Manažér sends it himself.
 *
 * Director 24.10.2026: „ak Agent sa narazí na tiket (napríklad MAGER-66) bez priloženého obsahu tak požiadá (tlačítko
 * ako to už máme v iných mistach) Poradcu aby doplnil chýbajúce informácie.“
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import { ApiError } from "@/services/api";
import { answerForDisplay, stepLabel } from "@/lib/poradcaAnswer";
import { takeHandoff } from "@/lib/poradcaHandoff";

const { attachApi } = vi.hoisted(() => ({ attachApi: vi.fn() }));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ selectedProject: { slug: "nex-knowledge" }, setSelectedVersion: vi.fn() }),
}));
vi.mock("@/services/api/poradca", () => ({
  stopPoradcaApi: vi.fn(),
  saveRequestToBacklogApi: vi.fn(),
  attachAgentTicketsApi: attachApi,
}));

import PoradcaAnswer from "@/components/poradca/PoradcaAnswer";

const VERSION = {
  id: "v-nk",
  version_number: "0.1.0",
  stage: "priprava",
  status: "awaiting_manazer",
  instruction_open: true,
  instruction_closed_reason: null,
};
const LINES =
  "Obsah tiketu MAGER-66 z Plane (Návrh NEX Knowledge): `private/MAGER-66.md` (4 kB) — leží v koreni projektu " +
  "v priečinku private/, mimo gitu.";

function answer(over: Record<string, unknown> = {}, scopeVersion: unknown = VERSION) {
  const message = {
    id: "m-1",
    author: "poradca",
    content: "Návrh je tiket MAGER-66.\n<tiket-pre-agenta>MAGER-66, DEV-186</tiket-pre-agenta>",
    steps: [],
    status: "done",
    instruction: null,
    backlog_request: null,
    captured_backlog_number: null,
    choices: [],
    ticket_proposal: null,
    plane_identifier: null,
    plane_url: null,
    agent_tickets: ["MAGER-66", "DEV-186"],
    ...over,
  };
  return render(
    <MemoryRouter initialEntries={["/poradca"]}>
      <Routes>
        <Route path="/poradca" element={<PoradcaAnswer message={message as never} scopeVersion={scopeVersion as never} />} />
        <Route path="/riadiace-centrum" element={<p>Riadiace centrum</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const button = () => screen.getByRole("button", { name: /Priložiť k odpovedi agentovi/ });

afterEach(() => {
  attachApi.mockReset();
  window.localStorage.clear();
});

describe("tickets for the agent under Poradca's answer (DEV-218)", () => {
  it("names the tickets it attaches", () => {
    answer();

    expect(button()).toHaveTextContent("Priložiť k odpovedi agentovi: MAGER-66, DEV-186");
    expect(button()).toBeEnabled();
  });

  it("one click attaches them and puts the lines into the answer for the agent in Riadiace centrum", async () => {
    attachApi.mockResolvedValue({ attached: ["private/MAGER-66.md"], answer_text: LINES });
    answer();

    fireEvent.click(button());

    expect(await screen.findByText("Riadiace centrum")).toBeInTheDocument();
    expect(attachApi).toHaveBeenCalledWith("m-1");
    expect(takeHandoff("v-nk")).toBe(LINES);
  });

  it("says why when the cockpit cannot read a ticket and hands nothing off", async () => {
    attachApi.mockRejectedValue(
      new ApiError(
        502,
        "Účet kokpitu nie je členom evidencie MAGER v Plane, preto tiket MAGER-66 neprečíta. O členstve kokpitu " +
          "v evidencii rozhoduje Director.",
      ),
    );
    answer();

    fireEvent.click(button());

    expect(await screen.findByText(/nie je členom evidencie MAGER v Plane/)).toBeInTheDocument();
    expect(screen.queryByText("Riadiace centrum")).not.toBeInTheDocument();
    expect(takeHandoff("v-nk")).toBeNull();
  });

  it("is grey with the reason when the conversation is about the whole project", () => {
    answer({}, null);

    expect(button()).toBeDisabled();
    expect(
      screen.getByText("Rozhovor je o celom projekte — hore vyber verziu, do ktorej stavby pokyn patrí."),
    ).toBeInTheDocument();
  });

  it("is not there when the answer names no ticket for the agent", () => {
    // with another button under the answer — the row of buttons is drawn, only this one is not
    answer({ agent_tickets: [], content: "Bez tiketu.", instruction: "Oprav test_login." });

    expect(screen.getByRole("button", { name: /Vložiť do Riadiaceho centra/ })).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: /Priložiť k odpovedi agentovi/ })).not.toBeInTheDocument();
  });

  it("a second click while the first runs attaches once", async () => {
    let finish: (v: unknown) => void = () => {};
    attachApi.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    answer();

    fireEvent.click(button());
    fireEvent.click(button());
    finish({ attached: [], answer_text: LINES });

    await waitFor(() => expect(screen.getByText("Riadiace centrum")).toBeInTheDocument());
    expect(attachApi).toHaveBeenCalledTimes(1);
  });
});

describe("the block and the step read as words (DEV-218)", () => {
  it("shows the block as a quote under its heading, not as a tag", () => {
    const shown = answerForDisplay("Návrh je tiket MAGER-66.\n<tiket-pre-agenta>MAGER-66, DEV-186</tiket-pre-agenta>");

    expect(shown).toContain("**Tiket pre agenta stavby:**\n\n> MAGER-66, DEV-186");
    expect(shown).not.toContain("<tiket-pre-agenta>");
  });

  it("says what Poradca does while it reads the ticket", () => {
    expect(stepLabel("plane_tiket", "MAGER-66")).toBe("Čítam tiket v Plane — MAGER-66");
  });
});

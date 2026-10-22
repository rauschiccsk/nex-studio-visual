/** DEV-88 — Poradca's answer and the agent's message link the project's files they name; the list is asked once. */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import PoradcaAnswer from "@/components/poradca/PoradcaAnswer";
import ConversationThread from "@/components/riadiace/ConversationThread";
import { forgetCodePaths } from "@/hooks/useCodePaths";
import type { PipelineMessage } from "@/services/api/pipeline";
import { getCodeFiles } from "@/services/api/nsCode";

vi.mock("@/services/api/nsCode", () => ({ getCodeFiles: vi.fn() }));
vi.mock("@/services/api/poradca", () => ({ stopPoradcaApi: vi.fn(), saveRequestToBacklogApi: vi.fn() }));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ selectedProject: { slug: "nex-inbox", name: "NEX Inbox" }, setSelectedVersion: vi.fn() }),
}));

beforeEach(() => {
  forgetCodePaths();
  vi.mocked(getCodeFiles)
    .mockReset()
    .mockResolvedValue({
      head: "abc",
      files: ["backend/apps/xml_exporter/ubl_builder.py"],
      changes: {},
      stamps: {},
      refresh_seconds: 10,
    });
});

const POINTER = "Kód je v backend/apps/xml_exporter/ubl_builder.py:214.";
const HREF = "/ns-code?path=backend%2Fapps%2Fxml_exporter%2Fubl_builder.py&line=214";

describe("files named by Poradca and the agent (DEV-88)", () => {
  it("Poradca's answers link the file at its line, with one question for all of them", async () => {
    const message = (id: string) => ({
      id,
      author: "poradca",
      content: POINTER,
      steps: [],
      status: "done",
      instruction: null,
      backlog_request: null,
      captured_backlog_number: null,
      choices: [],
    });
    render(
      <MemoryRouter>
        {["m1", "m2", "m3"].map((id) => (
          <PoradcaAnswer key={id} message={message(id) as never} scopeVersion={null} />
        ))}
      </MemoryRouter>,
    );

    const links = await screen.findAllByRole("link", { name: /ubl_builder\.py:214/ });
    expect(links.map((l) => l.getAttribute("href"))).toEqual([HREF, HREF, HREF]);
    expect(getCodeFiles).toHaveBeenCalledTimes(1);
    expect(getCodeFiles).toHaveBeenCalledWith("nex-inbox");
  });

  it("the agent's messages link it too, with one question for all of them", async () => {
    const msg = (id: string): PipelineMessage =>
      ({
        id,
        seq: Number(id),
        stage: "programovanie",
        author: "ai_agent",
        recipient: "manazer",
        kind: "gate_report",
        content: POINTER,
        payload: { report: POINTER },
        created_at: "2026-10-12T20:00:00Z",
      }) as unknown as PipelineMessage;
    render(
      <MemoryRouter>
        <ConversationThread messages={[msg("1"), msg("2"), msg("3")]} activity={[]} working={false} />
      </MemoryRouter>,
    );

    const links = await screen.findAllByRole("link", { name: /ubl_builder\.py:214/ });
    expect(links.length).toBeGreaterThanOrEqual(3);
    expect(links.every((l) => l.getAttribute("href") === HREF)).toBe(true);
    expect(getCodeFiles).toHaveBeenCalledTimes(1);
  });
});

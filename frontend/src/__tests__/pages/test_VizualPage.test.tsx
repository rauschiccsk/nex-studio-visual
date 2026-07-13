/**
 * VizualPage — the cockpit "Vizuál" surface (CR-1, nex-studio-visual; spec §3.D).
 *
 * Honest-by-construction: the page renders exactly one of four states off the pinned project/version + the live
 * board's `vizual_url` / `state.current_stage`. These tests pin the two load-bearing ones (the guard when
 * nothing is pinned; the live iframe when the URL is present in the vizual stage) plus the loading + not-in-
 * vizual notes. The store + WS hook are mocked the same way test_RiadiaceCentrumPage does.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import VizualPage from "@/pages/VizualPage";
import type { PipelineBoard } from "@/services/api/pipeline";

const { wsMock, contextMock } = vi.hoisted(() => ({
  wsMock: { board: null as import("@/services/api/pipeline").PipelineBoard | null },
  contextMock: {
    selectedProject: { slug: "demo", name: "Demo" } as { slug: string; name: string } | null,
    selectedVersion: { versionId: "v-1", versionNumber: "2.0.0" } as
      | { versionId: string; versionNumber: string }
      | null,
  },
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));
vi.mock("@/hooks/usePipelineWs", () => ({
  usePipelineWs: () => ({ ...wsMock }),
}));

const VIZUAL_URL = "https://vizual-demo.isnex.eu";

function boardWith(stage: string | null, vizualUrl?: string): PipelineBoard {
  return {
    state: stage ? { current_stage: stage } : null,
    recent_messages: [],
    vizual_url: vizualUrl ?? null,
  } as unknown as PipelineBoard;
}

describe("VizualPage — the cockpit Vizuál surface", () => {
  beforeEach(() => {
    wsMock.board = null;
    contextMock.selectedProject = { slug: "demo", name: "Demo" };
    contextMock.selectedVersion = { versionId: "v-1", versionNumber: "2.0.0" };
  });

  it("no project pinned → the pin-a-project guard (no iframe)", () => {
    contextMock.selectedProject = null;

    render(<VizualPage />);

    expect(screen.getByText("Nemáš vybraný projekt")).toBeInTheDocument();
    expect(screen.queryByTitle("Živý vizuál")).not.toBeInTheDocument();
  });

  it("in the vizual stage + vizual_url set → a sandboxed live iframe of the preview URL + header actions", () => {
    wsMock.board = boardWith("vizual", VIZUAL_URL);

    render(<VizualPage />);

    const frame = screen.getByTitle("Živý vizuál");
    expect(frame).toBeInTheDocument();
    expect(frame).toHaveAttribute("src", VIZUAL_URL);
    expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
    // "Otvoriť vo vlastnom okne" opens the preview in its own tab.
    const link = screen.getByRole("link", { name: /Otvoriť vo vlastnom okne/ });
    expect(link).toHaveAttribute("href", VIZUAL_URL);
    expect(link).toHaveAttribute("target", "_blank");
    // The reload affordance is present.
    expect(screen.getByRole("button", { name: /Obnoviť/ })).toBeInTheDocument();
  });

  it("in the vizual stage but no vizual_url yet → the 'spúšťa sa' loading state (no iframe)", () => {
    wsMock.board = boardWith("vizual"); // no URL recorded yet

    render(<VizualPage />);

    expect(screen.getByText("Živý náhľad sa spúšťa…")).toBeInTheDocument();
    expect(screen.queryByTitle("Živý vizuál")).not.toBeInTheDocument();
  });

  it("not in the vizual stage → a plain note pointing back to the Riadiace centrum (no iframe)", () => {
    wsMock.board = boardWith("programovanie", VIZUAL_URL); // a stale URL from a past run must NOT render live

    render(<VizualPage />);

    expect(screen.getByText("Živý vizuál tu zatiaľ nie je")).toBeInTheDocument();
    expect(screen.queryByTitle("Živý vizuál")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Otvor Riadiace centrum/ })).toBeInTheDocument();
  });
});

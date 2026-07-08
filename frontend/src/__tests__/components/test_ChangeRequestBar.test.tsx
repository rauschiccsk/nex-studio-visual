/**
 * ChangeRequestBar — the "Založiť novú verziu z tejto požiadavky" bar (konzultacia-mode.md Part 3; -followup Fix 3/4).
 *
 * Honest-by-construction gate (Fix 3): renders ONLY when the LATEST message carries an UN-captured
 * change_request marker AND the version is terminal (current_stage === 'done'). On click it captures the
 * request into a NEW draft version (the Part 2 endpoint, keyed on the source MESSAGE id), pins it, and
 * navigates to it using the RETURNED slug (Fix 4) so the Manažér reviews + starts the build deliberately.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import ChangeRequestBar from "@/components/riadiace/ChangeRequestBar";
import { captureChangeRequestApi } from "@/services/api/pipeline";
import type { PipelineBoard } from "@/services/api/pipeline";

const { navigateMock, contextMock } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  contextMock: {
    selectedProject: { slug: "demo", name: "Demo" } as { slug: string; name: string } | null,
    setSelectedVersion: vi.fn(),
  },
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock };
});
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));
vi.mock("@/services/api/pipeline", () => ({
  captureChangeRequestApi: vi.fn(),
}));

type Marker = { summary: string; title?: string; captured_version_id?: string | null };

// A board whose LATEST message (m1) carries `cr` (or none), on a terminal (done) version unless overridden.
function boardWith(cr: Marker | null, stage: string = "done"): PipelineBoard {
  return {
    state: { current_stage: stage, status: "done" },
    recent_messages: [
      { id: "m0", payload: { change_request: { summary: "starý", title: "old" } } }, // an OLDER marker — must be ignored
      { id: "m1", payload: cr ? { change_request: cr } : {} },
    ],
  } as unknown as PipelineBoard;
}

const LABEL = /Založiť novú verziu z tejto požiadavky/;

describe("ChangeRequestBar — change-request → new version", () => {
  beforeEach(() => {
    vi.mocked(captureChangeRequestApi).mockReset();
    navigateMock.mockReset();
    contextMock.setSelectedVersion.mockReset();
    contextMock.selectedProject = { slug: "demo", name: "Demo" };
  });

  it("renders NOTHING when the latest message carries no change_request marker (advisory follow-up clears it)", () => {
    // m1 (latest) has no marker even though an OLDER message (m0) does → the bar must stay hidden.
    const { container } = render(<ChangeRequestBar board={boardWith(null)} versionId="v-1" />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("button", { name: LABEL })).not.toBeInTheDocument();
  });

  it("renders NOTHING when the latest marker was already captured (idempotent — bar hidden)", () => {
    const { container } = render(
      <ChangeRequestBar board={boardWith({ summary: "X", captured_version_id: "nv-1" })} versionId="v-1" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders NOTHING mid-build (current_stage !== 'done'), even with a marker", () => {
    const { container } = render(
      <ChangeRequestBar board={boardWith({ summary: "X" }, "programovanie")} versionId="v-1" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the bar (button + summary) when the LATEST message carries an un-captured marker on a done version", () => {
    render(<ChangeRequestBar board={boardWith({ summary: "Pridať export do XLSX", title: "XLSX" })} versionId="v-1" />);
    expect(screen.getByRole("button", { name: LABEL })).toBeInTheDocument();
    expect(screen.getByText("Pridať export do XLSX")).toBeInTheDocument();
  });

  it("click captures via the source message id then pins + navigates to the new draft using the RETURNED slug", async () => {
    // The returned slug differs from the pinned project ("demo") to PROVE navigation uses the RETURNED slug (Fix 4).
    vi.mocked(captureChangeRequestApi).mockResolvedValue({
      version_id: "nv-9",
      version_number: "1.1.0",
      project_slug: "other-proj",
      backlog_number: 2,
    });
    render(<ChangeRequestBar board={boardWith({ summary: "Pridať export do XLSX", title: "XLSX" })} versionId="v-1" />);

    fireEvent.click(screen.getByRole("button", { name: LABEL }));

    await waitFor(() => expect(captureChangeRequestApi).toHaveBeenCalledWith("v-1", "m1"));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/projects/other-proj/versions/nv-9"));
    expect(contextMock.setSelectedVersion).toHaveBeenCalledWith({ versionId: "nv-9", versionNumber: "1.1.0" });
  });

  it("double-click captures ONLY once (synchronous double-submit guard)", async () => {
    vi.mocked(captureChangeRequestApi).mockResolvedValue({
      version_id: "nv-9",
      version_number: "1.1.0",
      project_slug: "demo",
      backlog_number: 2,
    });
    render(<ChangeRequestBar board={boardWith({ summary: "Pridať export do XLSX", title: "XLSX" })} versionId="v-1" />);

    const btn = screen.getByRole("button", { name: LABEL });
    fireEvent.click(btn);
    fireEvent.click(btn); // a second synchronous click before re-render must be a no-op

    await waitFor(() => expect(navigateMock).toHaveBeenCalled());
    expect(captureChangeRequestApi).toHaveBeenCalledTimes(1);
  });
});

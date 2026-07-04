/**
 * SpecifikaciaPage — the durable "Schválená / Rozpracované" badge (spine STEP 2 follow-up).
 *
 * The badge is derived from the board's DURABLE ``spec_approved`` flag (≥1 kind='approval' message),
 * NOT the truncated recent_messages tail:
 *   * spec_approved true                → "Schválená"      (takes precedence — approval implies a frozen spec)
 *   * a specification.md exists, not yet → "Rozpracované"
 *   * no spec on disk                    → no badge at all
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import SpecifikaciaPage from "@/pages/SpecifikaciaPage";

// ── Hoisted mocks ─────────────────────────────────────────────────────────────

const { getProjectSpecContentMock, getPipelineBoardApiMock, contextMock } = vi.hoisted(() => ({
  getProjectSpecContentMock: vi.fn(),
  getPipelineBoardApiMock: vi.fn(),
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
vi.mock("@/services/api/projectSpecs", () => ({ getProjectSpecContent: getProjectSpecContentMock }));
vi.mock("@/services/api/pipeline", () => ({ getPipelineBoardApi: getPipelineBoardApiMock }));

// ── Tests ───────────────────────────────────────────────────────────────────

describe("SpecifikaciaPage badge", () => {
  beforeEach(() => {
    getProjectSpecContentMock.mockReset();
    getPipelineBoardApiMock.mockReset();
  });

  it("shows 'Schválená' (and not 'Rozpracované') when spec_approved is true", async () => {
    getProjectSpecContentMock.mockResolvedValue({ is_text: true, content: "# Špecifikácia\n\nObsah." });
    getPipelineBoardApiMock.mockResolvedValue({ spec_approved: true });

    render(<SpecifikaciaPage />);

    expect(await screen.findByText("Schválená")).toBeInTheDocument();
    expect(screen.queryByText("Rozpracované")).not.toBeInTheDocument();
  });

  it("shows 'Rozpracované' when a spec exists but spec_approved is false", async () => {
    getProjectSpecContentMock.mockResolvedValue({ is_text: true, content: "# Špecifikácia\n\nObsah." });
    getPipelineBoardApiMock.mockResolvedValue({ spec_approved: false });

    render(<SpecifikaciaPage />);

    expect(await screen.findByText("Rozpracované")).toBeInTheDocument();
    expect(screen.queryByText("Schválená")).not.toBeInTheDocument();
  });

  it("shows NO badge when no spec exists on disk", async () => {
    getProjectSpecContentMock.mockRejectedValue(new Error("404"));
    getPipelineBoardApiMock.mockResolvedValue({ spec_approved: false });

    render(<SpecifikaciaPage />);

    // The empty-state prompt is the reliable "settled with no spec" anchor.
    await screen.findByText(/Špecifikácia zatiaľ nie je napísaná/);
    expect(screen.queryByText("Schválená")).not.toBeInTheDocument();
    expect(screen.queryByText("Rozpracované")).not.toBeInTheDocument();
  });

  it("stays un-approved when the board fetch fails (never falsely claims 'Schválená')", async () => {
    getProjectSpecContentMock.mockResolvedValue({ is_text: true, content: "# Špecifikácia\n\nObsah." });
    getPipelineBoardApiMock.mockRejectedValue(new Error("no pipeline"));

    render(<SpecifikaciaPage />);

    // A spec exists but the durable flag is unknown → "Rozpracované", never "Schválená".
    expect(await screen.findByText("Rozpracované")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Schválená")).not.toBeInTheDocument());
  });
});

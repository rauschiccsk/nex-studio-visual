/**
 * SpecifikaciaPage — the durable "Schválená / Rozpracované" badge (spine STEP 2 follow-up).
 *
 * The badge is derived from the board's DURABLE ``spec_approved`` flag (≥1 kind='approval' message) plus
 * whether a ``specification.md`` is actually on disk for the pinned version (via ``listProjectSpecs`` →
 * ``filterVersionDocs``), NOT the truncated recent_messages tail:
 *   * spec_approved true                → "Schválená"      (takes precedence — approval implies a frozen spec)
 *   * a specification.md exists, not yet → "Rozpracované"
 *   * no spec on disk                    → no badge at all
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import SpecifikaciaPage from "@/pages/SpecifikaciaPage";

// ── Hoisted mocks ─────────────────────────────────────────────────────────────

const { getProjectSpecContentMock, listProjectSpecsMock, getPipelineBoardApiMock, contextMock } = vi.hoisted(
  () => ({
    getProjectSpecContentMock: vi.fn(),
    listProjectSpecsMock: vi.fn(),
    getPipelineBoardApiMock: vi.fn(),
    contextMock: {
      selectedProject: { slug: "demo", name: "Demo" } as { slug: string; name: string } | null,
      selectedVersion: { versionId: "v-1", versionNumber: "2.0.0" } as
        | { versionId: string; versionNumber: string }
        | null,
    },
  }),
);

// A specification.md that survives ``filterVersionDocs`` for slug "demo" / version "2.0.0" → hasSpec = true.
const SPEC_DOC = {
  relative_path: "demo/docs/specs/versions/v2.0.0/specification.md",
  filename: "specification.md",
  category: "docs/specs/versions/v2.0.0",
  size_bytes: 42,
  is_directory: false,
};

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));
vi.mock("@/services/api/projectSpecs", () => ({
  getProjectSpecContent: getProjectSpecContentMock,
  listProjectSpecs: listProjectSpecsMock,
}));
vi.mock("@/services/api/pipeline", () => ({ getPipelineBoardApi: getPipelineBoardApiMock }));

// ── Tests ───────────────────────────────────────────────────────────────────

describe("SpecifikaciaPage badge", () => {
  beforeEach(() => {
    getProjectSpecContentMock.mockReset();
    listProjectSpecsMock.mockReset();
    getPipelineBoardApiMock.mockReset();
    // Default: the spec exists on disk and reads back as text (individual tests override the board flag).
    listProjectSpecsMock.mockResolvedValue({ documents: [SPEC_DOC], count: 1 });
    getProjectSpecContentMock.mockResolvedValue({ is_text: true, content: "# Špecifikácia\n\nObsah." });
  });

  it("shows 'Schválená' (and not 'Rozpracované') when spec_approved is true", async () => {
    getPipelineBoardApiMock.mockResolvedValue({ spec_approved: true });

    render(<SpecifikaciaPage />);

    expect(await screen.findByText("Schválená")).toBeInTheDocument();
    expect(screen.queryByText("Rozpracované")).not.toBeInTheDocument();
  });

  it("shows 'Rozpracované' when a spec exists but spec_approved is false", async () => {
    getPipelineBoardApiMock.mockResolvedValue({ spec_approved: false });

    render(<SpecifikaciaPage />);

    expect(await screen.findByText("Rozpracované")).toBeInTheDocument();
    expect(screen.queryByText("Schválená")).not.toBeInTheDocument();
  });

  it("shows NO badge when no spec exists on disk", async () => {
    listProjectSpecsMock.mockResolvedValue({ documents: [], count: 0 });
    getPipelineBoardApiMock.mockResolvedValue({ spec_approved: false });

    render(<SpecifikaciaPage />);

    // The empty-state prompt is the reliable "settled with no docs" anchor.
    await screen.findByText(/Zatiaľ tu nie sú žiadne dokumenty/);
    expect(screen.queryByText("Schválená")).not.toBeInTheDocument();
    expect(screen.queryByText("Rozpracované")).not.toBeInTheDocument();
  });

  it("stays un-approved when the board fetch fails (never falsely claims 'Schválená')", async () => {
    getPipelineBoardApiMock.mockRejectedValue(new Error("no pipeline"));

    render(<SpecifikaciaPage />);

    // A spec exists but the durable flag is unknown → "Rozpracované", never "Schválená".
    expect(await screen.findByText("Rozpracované")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Schválená")).not.toBeInTheDocument());
  });
});

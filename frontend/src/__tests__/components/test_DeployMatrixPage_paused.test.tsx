/**
 * DeployMatrixPage — the two things a live screenshot exposed on Nazar's UAT tab (v4.0.55).
 *
 * With v4.0.54 the notice explained WHY "Nasadiť" was closed, but two contradictions stayed on the same
 * screen, both instances of the same defect the notice was added to remove:
 *
 *   1. The intro still instructed "Nasaď overenú verziu … a klikni Akceptovať" — a sequence the manager
 *      cannot start — printed directly ABOVE the notice explaining that deployment is paused.
 *   2. "Akceptovať" rendered enabled for a project's owner-Junior, but acceptance is the ri-only PROD gate
 *      (v4.0.35/D3), so the click returned 403. A live-looking dead button is exactly what the batch bans.
 *
 * These pin both. The permission itself is NOT widened here — acceptance is what OPENS PROD, so it stays
 * with the Manažér; only its honesty on screen changes.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import DeployMatrixPage from "@/components/deploy/DeployMatrixPage";
import { getDeployMatrix } from "@/services/api/deploy";
import type { DeployBlockCause, DeployMatrix } from "@/types/deploy";

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock("@/services/api/deploy", () => ({
  getDeployMatrix: vi.fn(),
  deployCustomer: vi.fn(),
  acceptCustomerUat: vi.fn(),
  uatLaunch: vi.fn(),
}));

const contextMock = { selectedProject: { slug: "nex-websites", name: "NEX Websites" } };
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));

function matrix(over: Partial<DeployMatrix> = {}): DeployMatrix {
  return {
    project_slug: "nex-websites",
    auth_mode: "token",
    verified_versions: [],
    deployability: {
      cause: "awaiting_signoff" as DeployBlockCause,
      version_number: "0.1.0",
      version_id: "ver-1",
      can_reverify: false,
    },
    can_accept: false,
    rows: [
      {
        customer_id: "cust-1",
        customer_name: "ICC s.r.o.",
        customer_slug: "icc",
        subdomain: "icc",
        uat_version: "0.1.0",
        prod_version: null,
        uat_last_attempt_failed: false,
        prod_last_attempt_failed: false,
        accepted_versions: [],
        uat_url: "https://uat-icc-nex-websites.isnex.eu",
        prod_url: null,
      },
    ],
    ...over,
  };
}

describe("DeployMatrixPage — no instruction the manager cannot follow", () => {
  beforeEach(() => {
    vi.mocked(getDeployMatrix).mockReset();
  });

  it("drops the deploy→test→accept instruction while deployment is paused", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix());

    render(<DeployMatrixPage environment="uat" />);

    await waitFor(() => expect(screen.getByText(/Práve je pozastavené/)).toBeInTheDocument());
    // The impossible instruction must be GONE, not merely contradicted lower down.
    expect(screen.queryByText(/Nasaď overenú verziu, otestuj ju na UAT URL/)).not.toBeInTheDocument();
  });

  it("keeps the normal instruction when a version IS deployable", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(
      matrix({
        verified_versions: ["0.2.0"],
        deployability: { cause: "ok", version_number: null, version_id: null, can_reverify: false },
      }),
    );

    render(<DeployMatrixPage environment="uat" />);

    await waitFor(() =>
      expect(screen.getByText(/Nasaď overenú verziu, otestuj ju na UAT URL/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Práve je pozastavené/)).not.toBeInTheDocument();
  });

  it("disables Akceptovať WITH a reason for a user who may not accept (the ri-only PROD gate)", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix({ can_accept: false }));

    render(<DeployMatrixPage environment="uat" />);

    const accept = await screen.findByRole("button", { name: /Akceptovať/ });
    expect(accept).toBeDisabled();
    // …and the reason is VISIBLE, not hidden in a title on a disabled element (browsers suppress those).
    expect(screen.getByText(/akceptáciu robí Manažér/i)).toBeInTheDocument();
    expect(accept).toHaveAttribute("title", expect.stringContaining("iba Manažér"));
  });

  it("leaves Akceptovať live for the Manažér", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix({ can_accept: true }));

    render(<DeployMatrixPage environment="uat" />);

    const accept = await screen.findByRole("button", { name: /Akceptovať/ });
    expect(accept).toBeEnabled();
    expect(screen.queryByText(/akceptáciu robí Manažér/i)).not.toBeInTheDocument();
  });
});

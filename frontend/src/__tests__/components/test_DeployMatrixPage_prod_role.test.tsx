/**
 * DeployMatrixPage — the PROD "Nasadiť" must not look live to someone who may not deploy PROD.
 *
 * v4.0.55 fixed exactly this defect for "Akceptovať" (backend-computed `can_accept`, button disabled WITH
 * a visible reason) and stopped there. The deploy route carries its OWN ri-only gate for PROD
 * (`environment === "prod"` → 403 for anyone but the Manažér), while a UAT deploy is legitimately open to
 * the project's owner (D3) — so the button cannot be hidden by page-level role, and on the PROD tab a
 * Junior owner / a Medior went on seeing a live-looking button that 403-ed after the click.
 *
 * These pin the three states that matter: PROD blocked by role, PROD live for the Manažér, and UAT
 * untouched by the PROD gate.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import DeployMatrixPage from "@/components/deploy/DeployMatrixPage";
import { getDeployMatrix } from "@/services/api/deploy";
import type { DeployMatrix } from "@/types/deploy";

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

/** A project whose version IS deployable and IS accepted for this customer — so the ONLY possible
 *  block left is the role gate. Without this the test would pass for the wrong reason. */
function matrix(over: Partial<DeployMatrix> = {}): DeployMatrix {
  return {
    project_slug: "nex-websites",
    auth_mode: "password",
    verified_versions: ["0.2.0"],
    deployability: { cause: "ok", version_number: null, version_id: null, can_reverify: false },
    can_accept: false,
    can_deploy_prod: false,
    can_deploy: true,
    rows: [
      {
        customer_id: "cust-1",
        customer_name: "ICC s.r.o.",
        customer_slug: "icc",
        subdomain: "icc",
        uat_version: "0.2.0",
        prod_version: null,
        uat_last_attempt_failed: false,
        prod_last_attempt_failed: false,
        accepted_versions: ["0.2.0"],
        uat_url: "https://uat-icc-nex-websites.isnex.eu",
        prod_url: null,
      },
    ],
    ...over,
  };
}

describe("DeployMatrixPage — the PROD deploy role gate", () => {
  beforeEach(() => {
    vi.mocked(getDeployMatrix).mockReset();
  });

  it("disables the PROD Nasadiť WITH a visible reason when the user may not deploy PROD", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix({ can_deploy_prod: false }));

    render(<DeployMatrixPage environment="prod" />);

    const deploy = await screen.findByRole("button", { name: /Nasadiť/ });
    expect(deploy).toBeDisabled();
    // Visible, not only in a title — browsers suppress tooltips on disabled controls.
    expect(screen.getByText(/nasadzuje Manažér/i)).toBeInTheDocument();
    expect(deploy).toHaveAttribute("title", expect.stringContaining("iba Manažér"));
    // …and it must NOT blame the acceptance gate: this version IS accepted for this customer.
    expect(screen.queryByText(/čaká na akceptáciu UAT/i)).not.toBeInTheDocument();
  });

  it("leaves the PROD Nasadiť live for the Manažér", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix({ can_deploy_prod: true }));

    render(<DeployMatrixPage environment="prod" />);

    const deploy = await screen.findByRole("button", { name: /Nasadiť/ });
    await waitFor(() => expect(deploy).toBeEnabled());
    expect(screen.queryByText(/nasadzuje Manažér/i)).not.toBeInTheDocument();
  });

  it("does not apply the PROD role gate to the UAT tab (an owner deploys their own UAT)", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix({ can_deploy_prod: false }));

    render(<DeployMatrixPage environment="uat" />);

    const deploy = await screen.findByRole("button", { name: /Nasadiť/ });
    await waitFor(() => expect(deploy).toBeEnabled());
    expect(screen.queryByText(/nasadzuje Manažér/i)).not.toBeInTheDocument();
  });
});

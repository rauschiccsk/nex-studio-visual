/**
 * DeployMatrixPage — DEV-164: a Web project's installation is opened at each frontend's own address.
 *
 * The web takes the installation's main address, the administration ``…-admin``. The row links both, by the name
 * the Manažér knows them; a project with one frontend keeps its one „Otvoriť aplikáciu“ link.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import DeployMatrixPage from "@/components/deploy/DeployMatrixPage";
import { getDeployMatrix } from "@/services/api/deploy";
import type { DeployMatrix, DeployMatrixRow } from "@/types/deploy";

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock("@/services/api/deploy", () => ({
  getDeployMatrix: vi.fn(),
  deployCustomer: vi.fn(),
  acceptCustomerUat: vi.fn(),
  uatLaunch: vi.fn(),
  // DEV-115: each row asks whether a deploy runs on its installation; in these tests none does.
  getDeployProgress: vi.fn(() => Promise.resolve({ bezi: false, krok: null, trva_sekund: 0, krok_trva_sekund: 0 })),
  // DEV-90: the UAT tab also asks what to try; never answering keeps that panel out of these tests.
  getUatGuide: vi.fn(() => new Promise(() => {})),
  // DEV-91: the acceptance asks how much was tested; without an answer the question stays as before.
  getUatChecks: vi.fn(() => Promise.reject(new Error("not in this test"))),
}));

const contextMock = { selectedProject: { slug: "nex-websites", name: "NEX Websites" } };
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));

function matrix(row: Partial<DeployMatrixRow> = {}): DeployMatrix {
  return {
    project_slug: "career-asistent",
    auth_mode: "password",
    verified_versions: ["0.2.0"],
    deployability: { cause: "ok", version_number: null, version_id: null, can_reverify: false },
    can_accept: true,
    can_deploy_prod: true,
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
        uat_last_deploy_at: null,
        uat_last_deploy_detail: null,
        uat_last_deploy_warnings: [],
        prod_last_deploy_at: null,
        prod_last_deploy_detail: null,
        prod_last_deploy_warnings: [],
        accepted_versions: [],
        uat_url: "https://uat-icc-career-asistent.isnex.eu",
        prod_url: null,
        ...row,
      },
    ],
  };
}

beforeEach(() => {
  vi.mocked(getDeployMatrix).mockReset();
});

describe("DEV-164 — odkaz na každý frontend inštalácie", () => {
  it("a Web project's row opens the administration and the web at their own addresses", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(
      matrix({
        uat_urls: [
          { label: "Administrácia", url: "https://uat-icc-career-asistent-admin.isnex.eu" },
          { label: "Web", url: "https://uat-icc-career-asistent.isnex.eu" },
        ],
      }),
    );

    render(<DeployMatrixPage environment="uat" />);

    const admin = await screen.findByRole("link", { name: /Otvoriť: Administrácia/ });
    expect(admin).toHaveAttribute("href", "https://uat-icc-career-asistent-admin.isnex.eu");
    expect(screen.getByRole("link", { name: /Otvoriť: Web/ })).toHaveAttribute(
      "href",
      "https://uat-icc-career-asistent.isnex.eu",
    );
    expect(screen.queryByRole("link", { name: /Otvoriť aplikáciu/ })).toBeNull();
  });

  it("a project with one frontend keeps its one link", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix());

    render(<DeployMatrixPage environment="uat" />);

    expect(await screen.findByRole("link", { name: /Otvoriť aplikáciu/ })).toHaveAttribute(
      "href",
      "https://uat-icc-career-asistent.isnex.eu",
    );
    expect(screen.queryByRole("link", { name: /Otvoriť: / })).toBeNull();
  });
});

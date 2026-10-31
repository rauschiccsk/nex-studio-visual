/**
 * DeployMatrixPage — the PROD page says at each customer's row which machine PROD goes to (DEV-274).
 *
 * 31.10.2026 the Director asked „A odkiaľ náš kokpit vie, že kam má nasadiť prod?“ — the machine lived only in the
 * database. The UAT page does not say it: UAT always runs on this machine.
 */
/* Harness as in test_DeployMatrixPage_adoption_is_reachable. */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
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
  // The UAT page also asks for its guide of changes to try — never answered here, it is not what is tested.
  getUatGuide: vi.fn(() => new Promise(() => {})),
  // DEV-91: the acceptance asks how much was tested; not what is tested here.
  getUatChecks: vi.fn(() => Promise.reject(new Error("not in this test"))),
  // DEV-115: each row asks whether a deploy runs on its installation; in these tests none does.
  getDeployProgress: vi.fn(() =>
    Promise.resolve({
      bezi: false,
      krok: null,
      trva_sekund: 0,
      krok_trva_sekund: 0,
    }),
  ),
}));

const contextMock = {
  selectedProject: { slug: "nex-inbox", name: "NEX Inbox" },
};
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) =>
    selector(contextMock),
}));

function matrix(
  over: {
    prod_version?: string | null;
    prod_last_attempt_failed?: boolean;
  } = {},
): DeployMatrix {
  return {
    project_slug: "nex-inbox",
    auth_mode: "password",
    verified_versions: ["1.5.6"],
    deployability: {
      cause: "ok",
      version_number: null,
      version_id: null,
      can_reverify: false,
    },
    can_accept: true,
    can_deploy_prod: true,
    can_deploy: true,
    rows: [
      {
        customer_id: "cust-1",
        customer_name: "MÁGERSTAV",
        customer_slug: "mager",
        subdomain: "magerstav",
        uat_version: "1.5.6",
        prod_version: over.prod_version ?? null,
        uat_last_attempt_failed: false,
        prod_last_attempt_failed: over.prod_last_attempt_failed ?? false,
        uat_last_deploy_at: null,
        uat_last_deploy_detail: null,
        uat_last_deploy_warnings: [],
        prod_last_deploy_at: null,
        prod_last_deploy_detail: null,
        prod_last_deploy_warnings: [],
        accepted_versions: ["1.5.6"],
        uat_url: null,
        prod_url: null,
        prod_machine: "MAGER",
      },
    ],
  };
}

beforeEach(() => {
  vi.mocked(getDeployMatrix).mockReset();
});

describe("the machine PROD goes to (DEV-274)", () => {
  it("is named at the row on the PROD page", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(
      matrix({ prod_version: "1.5.6" }),
    );

    render(<DeployMatrixPage environment="prod" />);

    expect(await screen.findByText("na stroji MAGER")).toBeInTheDocument();
  });

  it("is not named on the UAT page — UAT runs here", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(
      matrix({ prod_version: "1.5.6" }),
    );

    render(<DeployMatrixPage environment="uat" />);

    expect(await screen.findByText("MÁGERSTAV")).toBeInTheDocument();
    expect(screen.queryByText(/na stroji/)).not.toBeInTheDocument();
  });
});

/**
 * DEV-247 — „Akceptovať“ asks with what was tried in every version the UAT carries that the customer has not accepted.
 *
 * 28.10.2026 NEX Manager for MÁGERSTAV: UAT on 1.4.1 carried the never-accepted 1.3.0 and 1.4.0; the acceptance
 * question counted the checks of 1.4.1 alone, which had none.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import DeployMatrixPage from "@/components/deploy/DeployMatrixPage";
import { acceptCustomerUat, getDeployMatrix, getUatChecks } from "@/services/api/deploy";
import type { DeployMatrix, UatProgress } from "@/types/deploy";

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock("@/services/api/deploy", () => ({
  getDeployMatrix: vi.fn(),
  deployCustomer: vi.fn(),
  acceptCustomerUat: vi.fn(),
  uatLaunch: vi.fn(),
  getDeployProgress: vi.fn(() => Promise.resolve({ bezi: false, krok: null, trva_sekund: 0, krok_trva_sekund: 0 })),
  getUatGuide: vi.fn(() => new Promise(() => {})),
  getUatChecks: vi.fn(),
}));

const contextMock = { selectedProject: { slug: "nex-manager", name: "NEX Manager" } };
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));

const MATRIX = {
  project_slug: "nex-manager",
  auth_mode: "password",
  verified_versions: ["1.4.1", "1.2.2"],
  deployability: { cause: "ok", version_number: null, version_id: null, can_reverify: false },
  can_accept: true,
  can_deploy_prod: true,
  can_deploy: true,
  rows: [
    {
      customer_id: "mager",
      customer_name: "MÁGERSTAV",
      customer_slug: "mager",
      subdomain: "mager",
      uat_version: "1.4.1",
      prod_version: "1.2.2",
      uat_last_attempt_failed: false,
      prod_last_attempt_failed: false,
      uat_last_deploy_at: null,
      uat_last_deploy_detail: null,
      uat_last_deploy_warnings: [],
      prod_last_deploy_at: null,
      prod_last_deploy_detail: null,
      prod_last_deploy_warnings: [],
      accepted_versions: ["1.2.2"],
      uat_versions_to_test: ["1.4.0", "1.4.1"],
      uat_url: null,
      prod_url: null,
    },
  ],
} as unknown as DeployMatrix;

const PROGRESS: Record<string, UatProgress> = {
  "1.4.0": {
    version_number: "1.4.0",
    items: ["Založenie skupiny", "Pridelenie aplikácie skupine"],
    checks: [{ item: "Založenie skupiny", status: "tested", note: null, checked_by: "Z", updated_at: "2026-10-28T12:00:00Z" }],
    tested: 1,
    total: 2,
    untested: ["Pridelenie aplikácie skupine"],
    broken: [],
  } as unknown as UatProgress,
  "1.4.1": { version_number: "1.4.1", items: [], checks: [], tested: 0, total: 0, untested: [], broken: [] } as unknown as UatProgress,
};

beforeEach(() => {
  vi.mocked(getDeployMatrix).mockReset().mockResolvedValue(MATRIX);
  vi.mocked(acceptCustomerUat).mockReset();
  vi.mocked(getUatChecks).mockReset().mockImplementation((_c, version) => Promise.resolve(PROGRESS[version] as UatProgress));
});

describe("Akceptovať counts every version not accepted (DEV-247)", () => {
  it("names what was tried and what was not in each of them", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<DeployMatrixPage environment="uat" />);

    fireEvent.click(await screen.findByRole("button", { name: /Akceptovať/ }));

    await waitFor(() => expect(confirm).toHaveBeenCalled());
    const question = confirm.mock.calls[0]?.[0] ?? "";
    expect(question).toContain("Otestované 1 z 2.");
    expect(question).toContain("Neotestované: 1.4.0: Pridelenie aplikácie skupine.");
    // the UAT guide panel on the same page asks too — what counts is that every version was asked about
    expect([...new Set(vi.mocked(getUatChecks).mock.calls.map((c) => c[1]))].sort()).toEqual(["1.4.0", "1.4.1"]);
    expect(acceptCustomerUat).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
});

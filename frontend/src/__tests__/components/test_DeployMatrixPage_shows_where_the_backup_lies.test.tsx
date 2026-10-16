/**
 * DeployMatrixPage — the row says where the database backup taken before the last deploy lies (DEV-116).
 *
 * The backend puts the sentence into the deploy's report, in its first part — the part the row did not show (it
 * shows only the warnings after „ | “). Without this line the backup exists but nobody on the screen knows where.
 */

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
  getDeployProgress: vi.fn(() => Promise.resolve({ bezi: false, krok: null, trva_sekund: 0, krok_trva_sekund: 0 })),
  deployCustomer: vi.fn(),
  acceptCustomerUat: vi.fn(),
  uatLaunch: vi.fn(),
}));

const contextMock = { selectedProject: { slug: "nex-inbox", name: "NEX Inbox" } };
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));

const BACKUP =
  "Záloha databázy pred nasadením: /opt/customers/mager/nex-inbox/pre-deploy-backups/nex-inbox-1.8.1-20261015T010000Z.dump " +
  "(1.4 MB, 52 položiek, sha256 abababababab…; obnova: pg_restore --clean --if-exists v databázovom kontajneri inštalácie). " +
  "Ponechaných je posledných 5 záloh.";

function matrix(detail: string | null, warnings: string[] = []): DeployMatrix {
  return {
    project_slug: "nex-inbox",
    auth_mode: "password",
    verified_versions: ["1.8.1"],
    deployability: { cause: "ok", version_number: null, version_id: null, can_reverify: false },
    can_accept: true,
    can_deploy_prod: true,
    can_deploy: true,
    rows: [
      {
        customer_id: "cust-1",
        customer_name: "MÁGERSTAV",
        customer_slug: "mager",
        subdomain: "magerstav",
        uat_version: "1.8.1",
        prod_version: "1.8.1",
        uat_last_attempt_failed: false,
        prod_last_attempt_failed: false,
        uat_last_deploy_at: null,
        uat_last_deploy_detail: null,
        uat_last_deploy_warnings: [],
        prod_last_deploy_at: "2026-10-15T01:05:00Z",
        prod_last_deploy_detail: detail,
        prod_last_deploy_warnings: warnings,
        accepted_versions: ["1.8.1"],
        uat_url: null,
        prod_url: null,
      },
    ],
  };
}

beforeEach(() => {
  vi.mocked(getDeployMatrix).mockReset();
});

describe("Riadok PROD povie, kde leží záloha pred nasadením", () => {
  it("⚠️ veta o zálohe z hlásenia nasadenia je v riadku, bez upratovania a vedľa upozornení", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(
      matrix(`OK ${BACKUP} Upratané na cieli — vyrovnávacia pamäť stavby: 0B.`, ["Kontrola po nasadení: pomalá odpoveď"]),
    );

    render(<DeployMatrixPage environment="prod" />);

    expect(await screen.findByText(BACKUP)).toBeInTheDocument();
    expect(screen.queryByText(/Upratané na cieli/)).not.toBeInTheDocument();
    expect(screen.getByText(/Kontrola po nasadení: pomalá odpoveď/)).toBeInTheDocument();
  });

  it("bez zálohy v hlásení riadok nič nevymýšľa", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix("OK"));

    render(<DeployMatrixPage environment="prod" />);

    expect(await screen.findByText(/nasadené/)).toBeInTheDocument();
    expect(screen.queryByText(/Záloha databázy/)).not.toBeInTheDocument();
  });
});

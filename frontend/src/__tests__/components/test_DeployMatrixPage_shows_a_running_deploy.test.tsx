/**
 * DeployMatrixPage — a deploy that runs on the server is visible in its row, whoever started it and whenever the
 * page was opened (DEV-115).
 *
 * Director 14.10.2026, mid-deploy of NEX Inbox 1.8.0 to MÁGERSTAV's PROD: „Nasadenie sa spustilo. Potom som
 * preklikol na záložku Zásobník, a keď som sa vrátil … Z toho mi nie je jasné či nasadenie sa prerušilo alebo je
 * to v poriadku.“ The row showed the old version, „Posledný pokus zlyhal“ and a live „Nasadiť“: the page knew about
 * the deploy only from its own click, and leaving the page forgot the click. The cockpit itself knew the step and the
 * time all along (deploy-progress, ICCINT-153) — only the adoption dialog asked.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import DeployMatrixPage from "@/components/deploy/DeployMatrixPage";
import { deployCustomer, getDeployMatrix, getDeployProgress, type DeployProgress } from "@/services/api/deploy";
import type { DeployMatrix } from "@/types/deploy";

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock("@/services/api/deploy", () => ({
  getDeployMatrix: vi.fn(),
  getDeployProgress: vi.fn(),
  deployCustomer: vi.fn(),
  acceptCustomerUat: vi.fn(),
  uatLaunch: vi.fn(),
  getUatGuide: vi.fn(() => new Promise(() => {})),
  getUatChecks: vi.fn(() => new Promise(() => {})),
}));

const contextMock = { selectedProject: { slug: "nex-inbox", name: "NEX Inbox" } };
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));

function matrix(prodVersion: string, failed: boolean): DeployMatrix {
  return {
    project_slug: "nex-inbox",
    auth_mode: "password",
    verified_versions: ["1.8.0"],
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
        uat_version: "1.8.0",
        prod_version: prodVersion,
        uat_last_attempt_failed: false,
        prod_last_attempt_failed: failed,
        uat_last_deploy_at: null,
        uat_last_deploy_detail: null,
        uat_last_deploy_warnings: [],
        prod_last_deploy_at: null,
        prod_last_deploy_detail: null,
        prod_last_deploy_warnings: [],
        accepted_versions: ["1.8.0"],
        uat_url: null,
        prod_url: null,
      },
    ],
  };
}

const BUILDING: DeployProgress = {
  bezi: true,
  krok: "prenášam zdrojový kód a staviam obrazy — bežiaca inštalácia sa zatiaľ nemení",
  trva_sekund: 780,
  krok_trva_sekund: 700,
};
const IDLE: DeployProgress = { bezi: false, krok: null, trva_sekund: 0, krok_trva_sekund: 0 };
const RUNNING_REASON = "Na tejto inštalácii práve beží nasadenie — počkaj, kým skončí.";

/** What the server says about the deploy right now. The test switches it — never the order of answers: with
 * ``shouldAdvanceTime`` real time runs the 2 s polls too, and on a slow machine a ``…Once`` answer was used up before
 * the page was looked at (CI 15.10.2026, locally 2 of 20 runs). */
let progress: DeployProgress;
let current: DeployMatrix;

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(getDeployMatrix).mockReset();
  vi.mocked(getDeployProgress).mockReset();
  progress = IDLE;
  current = matrix("1.7.1", false);
  vi.mocked(getDeployMatrix).mockImplementation(async () => current);
  vi.mocked(getDeployProgress).mockImplementation(async () => progress);
});
afterEach(() => {
  vi.useRealTimers();
});

describe("Bežiace nasadenie je vidieť v riadku", () => {
  it("⚠️ stránka otvorená počas nasadenia ukáže krok a čas a nič sa nedá spustiť znova", async () => {
    current = matrix("1.5.6", true);
    progress = BUILDING;

    render(<DeployMatrixPage environment="prod" />);

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Nasadenie beží: prenášam zdrojový kód a staviam obrazy — bežiaca inštalácia sa zatiaľ nemení · 13 min",
    );
    expect(getDeployProgress).toHaveBeenCalledWith("cust-1", "prod");
    const deploy = screen.getByRole("button", { name: /Nasadiť/ });
    expect(deploy).toBeDisabled();
    expect(deploy).toHaveAttribute("title", RUNNING_REASON);
    const adopt = screen.getByRole("button", { name: /Prevziať inštaláciu/ });
    expect(adopt).toBeDisabled();
    expect(adopt).toHaveAttribute("title", RUNNING_REASON);
    // One line about one deploy — the click's guess „~2 min“ would contradict the measured 13 min.
    expect(screen.queryByText(/Nasadzujem…/)).not.toBeInTheDocument();
  });

  it("po vlastnom kliku stojí odhad len dovtedy, kým server nepovie krok a čas", async () => {
    vi.mocked(deployCustomer).mockReturnValue(new Promise(() => {}));

    render(<DeployMatrixPage environment="prod" />);
    fireEvent.click(await screen.findByRole("button", { name: /Nasadiť/ }));

    expect(screen.getByText(/Nasadzujem…/)).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    progress = BUILDING;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });

    expect(screen.getByRole("status")).toHaveTextContent("Nasadenie beží: prenášam zdrojový kód");
    expect(screen.queryByText(/Nasadzujem…/)).not.toBeInTheDocument();
  });

  it("keď nasadenie skončí, riadok sa sám obnoví na výsledok", async () => {
    current = matrix("1.5.6", true);
    progress = BUILDING;

    render(<DeployMatrixPage environment="prod" />);
    expect(await screen.findByRole("status")).toBeInTheDocument();
    const loaded = vi.mocked(getDeployMatrix).mock.calls.length;

    current = matrix("1.8.0", false);
    progress = IDLE;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(getDeployMatrix).toHaveBeenCalledTimes(loaded + 1);
    expect(await screen.findByText("Nasadiť znova")).toBeInTheDocument();
    expect(screen.queryByText("Posledný pokus zlyhal")).not.toBeInTheDocument();
  });

  it("nasadenie spustené inde sa ukáže aj na stránke, ktorá je už otvorená", async () => {
    render(<DeployMatrixPage environment="prod" />);
    expect(await screen.findByRole("button", { name: /Nasadiť/ })).toBeEnabled();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    progress = BUILDING;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15100);
    });

    expect(screen.getByRole("status")).toHaveTextContent("Nasadenie beží:");
    expect(screen.getByRole("button", { name: /Nasadiť/ })).toBeDisabled();
  });

  it("výpadok otázky na priebeh stránku nezhodí", async () => {
    vi.mocked(getDeployProgress).mockRejectedValue(new Error("network"));

    render(<DeployMatrixPage environment="prod" />);

    expect(await screen.findByRole("button", { name: /Nasadiť/ })).toBeEnabled();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

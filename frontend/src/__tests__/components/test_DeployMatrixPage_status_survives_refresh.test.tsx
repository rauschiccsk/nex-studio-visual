/**
 * DeployMatrixPage — ICCINT-117: stav, ktorý platí len do prvého F5, nie je stav.
 *
 * Director 11.09.2026, po nasadení NEX Managera 1.2.1 na MÁGERSTAV UAT: „po F5 informácie
 * o nasadení zmizli a systém ponúka nasadiť tú istú verziu znovu.“
 *
 * Zelené „✓ Nasadené“ aj upozornenia žili iba v premennej `result` — odpovedi na klik — takže
 * refresh ich zmazal. Zostal riadok, ktorý ukazoval „Verzia na UAT: 1.2.1“, v zozname vybranú
 * 1.2.1 a vedľa plnofarebné „Nasadiť“, pričom NIKDE nestálo, že tá verzia už tam je. Obrazovka
 * teda nezabudla — ona to nikdy nepovedala.
 *
 * Tieto skúšky kreslia stránku BEZ jediného kliknutia (presne stav po F5).
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

const contextMock = { selectedProject: { slug: "nex-manager", name: "NEX Manager" } };
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));

/** Presne Directorov prípad: 1.2.1 je nasadená a je to zároveň najnovšia overená verzia. */
function matrixAfterDeploy(over: Partial<DeployMatrix["rows"][0]> = {}): DeployMatrix {
  return {
    project_slug: "nex-manager",
    auth_mode: "password",
    verified_versions: ["1.2.1", "1.2.0"],
    deployability: { cause: "ok", version_number: null, version_id: null, can_reverify: false },
    can_accept: true,
    can_deploy_prod: true,
    can_deploy: true,
    rows: [
      {
        customer_id: "cust-1",
        customer_name: "MÁGERSTAV s.r.o.",
        customer_slug: "mager",
        subdomain: "mager",
        uat_version: "1.2.1",
        prod_version: null,
        uat_last_attempt_failed: false,
        prod_last_attempt_failed: false,
        uat_last_deploy_at: "2026-09-11T02:24:02Z",
        uat_last_deploy_detail: "OK",
        prod_last_deploy_at: null,
        prod_last_deploy_detail: null,
        accepted_versions: [],
        uat_url: "https://uat-mager-manager.isnex.eu",
        prod_url: null,
        ...over,
      },
    ],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ICCINT-117 — po refreshi stav nezmizne", () => {
  it("bez jediného kliknutia je na obrazovke, KEDY sa naposledy nasadilo", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrixAfterDeploy());
    render(<DeployMatrixPage environment="uat" />);

    await screen.findByText("MÁGERSTAV s.r.o.");
    // Nestačí hľadať verziu — tá tam bola aj predtým. Musí byť vidieť ČAS.
    await waitFor(() => {
      expect(screen.getByText(/nasadené/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/11\.\s*9\.|2026/)).toBeInTheDocument();
  });

  it("tlačidlo povie, že ide o OPAKOVANÉ nasadenie tej istej verzie", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrixAfterDeploy());
    render(<DeployMatrixPage environment="uat" />);

    await screen.findByText("MÁGERSTAV s.r.o.");
    // Vybraná verzia (najnovšia overená) === nasadená verzia → nie je to ďalší krok.
    expect(await screen.findByRole("button", { name: /Nasadiť znova/i })).toBeInTheDocument();
  });

  it("pri INEJ verzii zostáva tlačidlo obyčajným „Nasadiť“", async () => {
    // Nasadená je staršia 1.2.0, ponúka sa novšia 1.2.1 → normálny ďalší krok.
    vi.mocked(getDeployMatrix).mockResolvedValue(matrixAfterDeploy({ uat_version: "1.2.0" }));
    render(<DeployMatrixPage environment="uat" />);

    await screen.findByText("MÁGERSTAV s.r.o.");
    expect(await screen.findByRole("button", { name: /^Nasadiť$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Nasadiť znova/i })).toBeNull();
  });

  it("upozornenia z nasadenia sa dajú prečítať aj po refreshi", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(
      matrixAfterDeploy({ uat_last_deploy_detail: "OK | prihlasovací lístok sa nepodarilo vydať" }),
    );
    render(<DeployMatrixPage environment="uat" />);

    await screen.findByText("MÁGERSTAV s.r.o.");
    expect(await screen.findByText(/prihlasovací lístok sa nepodarilo vydať/i)).toBeInTheDocument();
  });
});

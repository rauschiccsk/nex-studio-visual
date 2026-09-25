/**
 * DeployMatrixPage — k prevzatiu inštalácie sa treba dostať BEZ toho, aby niečo najprv zlyhalo (ICCINT-151).
 *
 * Tlačidlo „Prevziať inštaláciu…" sa zobrazovalo len vtedy, keď predošlý pokus o nasadenie zlyhal.
 * Znamenalo to, že človek musel najprv vyrobiť červený záznam, aby sa dostal k správnej ceste — a presne
 * to sa 25.09.2026 stalo Directorovi: *„Nasadil som, zlyhalo — tlačidlo na prevzatie sa objavilo, stlačil
 * som."* Cez Tiborovu optiku: keby to robil on, musel by si najprv pokaziť nasadenie, aby zistil, ako sa
 * to robí správne. To je diera v kokpite, nie jeho chyba.
 *
 * ⚠️ Čo tieto stráže NEDOVOLIA zmeniť:
 *  - inštaláciu, o ktorej kokpit nevie (nemá zapísanú nasadenú verziu), sa DÁ prevziať hneď;
 *  - po zlyhanom pokuse tá cesta zostáva, ako bola;
 *  - na riadku, ktorý kokpit už spravuje, tlačidlo NIE JE — tam niet čo preberať a bola by to pozvánka
 *    prepísať bežiacu inštaláciu.
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
  deployCustomer: vi.fn(),
  acceptCustomerUat: vi.fn(),
  uatLaunch: vi.fn(),
}));

const contextMock = { selectedProject: { slug: "nex-inbox", name: "NEX Inbox" } };
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));

function matrix(over: { prod_version?: string | null; prod_last_attempt_failed?: boolean } = {}): DeployMatrix {
  return {
    project_slug: "nex-inbox",
    auth_mode: "password",
    verified_versions: ["1.5.6"],
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
        uat_version: "1.5.6",
        prod_version: over.prod_version ?? null,
        uat_last_attempt_failed: false,
        prod_last_attempt_failed: over.prod_last_attempt_failed ?? false,
        uat_last_deploy_at: null,
        uat_last_deploy_detail: null,
        prod_last_deploy_at: null,
        prod_last_deploy_detail: null,
        accepted_versions: ["1.5.6"],
        uat_url: null,
        prod_url: null,
      },
    ],
  };
}

const PREVZIAT = /Prevziať inštaláciu/;

beforeEach(() => {
  vi.mocked(getDeployMatrix).mockReset();
});

describe("Prevzatie inštalácie je dosiahnuteľné bez zlyhania", () => {
  it("⚠️ inštalácia, o ktorej kokpit nevie, sa dá prevziať hneď", async () => {
    // Nasadená verzia nie je zapísaná — čiže kokpit nevie, čo tam beží. Presne to je stav ručne
    // písanej inštalácie a presne vtedy je prevzatie ten správny ďalší krok.
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix({ prod_version: null, prod_last_attempt_failed: false }));

    render(<DeployMatrixPage environment="prod" />);

    expect(await screen.findByRole("button", { name: PREVZIAT })).toBeInTheDocument();
  });

  it("po zlyhanom pokuse tá cesta zostáva", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix({ prod_version: "1.5.5", prod_last_attempt_failed: true }));

    render(<DeployMatrixPage environment="prod" />);

    expect(await screen.findByRole("button", { name: PREVZIAT })).toBeInTheDocument();
  });

  it("⚠️ na inštalácii, ktorú kokpit už spravuje, tlačidlo NIE JE", async () => {
    // Niet čo preberať — a tlačidlo by tu bolo pozvánkou prepísať bežiacu inštaláciu zákazníka.
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix({ prod_version: "1.5.6", prod_last_attempt_failed: false }));

    render(<DeployMatrixPage environment="prod" />);

    expect(await screen.findByText("MÁGERSTAV")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: PREVZIAT })).not.toBeInTheDocument();
  });
});

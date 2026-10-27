/**
 * DEV-238 — „Postaviť nanovo“ on UAT: offered only where an installation exists, run only after an explicit
 * consent, and the request carries force_fresh.
 *
 * 26.10.2026 the first deploy of Author Asistent left its UAT installation broken, a redeploy keeps an installation's
 * settings (ICCINT-133), and nothing on the screen could build it again. Director 27.10.2026: „Súhlasím“ — the button
 * only on UAT, asking first whether the UAT data may go; never on a production installation.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import DeployMatrixPage from "@/components/deploy/DeployMatrixPage";
import { deployCustomer, getDeployMatrix } from "@/services/api/deploy";
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
  getDeployProgress: vi.fn(() => Promise.resolve({ bezi: false, krok: null, trva_sekund: 0, krok_trva_sekund: 0 })),
  getUatGuide: vi.fn(() => new Promise(() => {})),
  getUatChecks: vi.fn(() => new Promise(() => {})),
}));

const contextMock = { selectedProject: { slug: "author-asistent", name: "Author Asistent" } };
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));

function matrix(uatVersion: string | null): DeployMatrix {
  return {
    project_slug: "author-asistent",
    auth_mode: "password",
    verified_versions: ["0.1.0"],
    deployability: { cause: "ok", version_number: null, version_id: null, can_reverify: false },
    can_accept: true,
    can_deploy_prod: true,
    can_deploy: true,
    rows: [
      {
        customer_id: "cust-1",
        customer_name: "Tata",
        customer_slug: "tata",
        subdomain: "tata",
        uat_version: uatVersion,
        prod_version: "0.1.0",
        uat_last_attempt_failed: false,
        prod_last_attempt_failed: false,
        uat_last_deploy_at: null,
        uat_last_deploy_detail: null,
        uat_last_deploy_warnings: [],
        prod_last_deploy_at: null,
        prod_last_deploy_detail: null,
        prod_last_deploy_warnings: [],
        accepted_versions: ["0.1.0"],
        uat_url: null,
        prod_url: null,
      },
    ],
  } as DeployMatrix;
}

const NANOVO = /Postaviť nanovo/;
const POTVRDIT = "Zahodiť dáta a postaviť nanovo";

beforeEach(() => {
  vi.mocked(getDeployMatrix).mockReset();
  vi.mocked(deployCustomer).mockReset();
});

describe("Postaviť nanovo (DEV-238)", () => {
  it("a UAT row with an installation offers it", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix("0.1.0"));

    render(<DeployMatrixPage environment="uat" />);

    expect(await screen.findByRole("button", { name: NANOVO })).toBeInTheDocument();
  });

  it("a UAT row without an installation does not — there is nothing to build again", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix(null));

    render(<DeployMatrixPage environment="uat" />);

    expect(await screen.findByText("Tata")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: NANOVO })).not.toBeInTheDocument();
  });

  it("the production tab never offers it", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix("0.1.0"));

    render(<DeployMatrixPage environment="prod" />);

    expect(await screen.findByText("Tata")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: NANOVO })).not.toBeInTheDocument();
  });

  it("it asks first, and builds anew only after the explicit consent", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix("0.1.0"));
    vi.mocked(deployCustomer).mockResolvedValue({ ok: true, event: {}, url: null, warnings: [] } as never);
    render(<DeployMatrixPage environment="uat" />);

    fireEvent.click(await screen.findByRole("button", { name: NANOVO }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("databázou");
    const confirm = screen.getByRole("button", { name: POTVRDIT });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(deployCustomer).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: POTVRDIT }));

    await waitFor(() =>
      expect(deployCustomer).toHaveBeenCalledWith("cust-1", {
        version_number: "0.1.0",
        environment: "uat",
        force_fresh: true,
      }),
    );
  });

  it("cancelling leaves the installation alone", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix("0.1.0"));
    render(<DeployMatrixPage environment="uat" />);

    fireEvent.click(await screen.findByRole("button", { name: NANOVO }));
    fireEvent.click(screen.getByRole("button", { name: "Zrušiť" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(deployCustomer).not.toHaveBeenCalled();
  });

  it("a plain Nasadiť sends no force_fresh", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix("0.1.0"));
    vi.mocked(deployCustomer).mockResolvedValue({ ok: true, event: {}, url: null, warnings: [] } as never);
    render(<DeployMatrixPage environment="uat" />);

    fireEvent.click(await screen.findByRole("button", { name: /^Nasadiť/ }));

    await waitFor(() =>
      expect(deployCustomer).toHaveBeenCalledWith("cust-1", { version_number: "0.1.0", environment: "uat" }),
    );
  });
});

/**
 * DeployMatrixPage — DEV-129: every warning of a deploy is said once, in Slovak, with no technical prefix.
 *
 * Director 15.10.2026, after deploying NEX Websites to PROD: the same warnings stood twice — in the column of the
 * deployed version (split back out of the event's text, behind an English „warnings:“) and under the deploy button
 * (the click's answer). The row now reads them from a field of their own; the block under the button says them only
 * for a failed deploy, which is not the row's last good one.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import DeployMatrixPage from "@/components/deploy/DeployMatrixPage";
import { deployCustomer, getDeployMatrix } from "@/services/api/deploy";
import type { DeployEnvironment, DeployMatrix, DeployMatrixRow, DeployResult } from "@/types/deploy";

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

const LAUNCH = "Spustenie jedným kliknutím zatiaľ nefunguje";
const HEALTH = "Po nasadení aplikácia hlási problém, ktorý predtým nemala";
const OLD = "Upozornenie predošlého nasadenia";
const FAILED = "Nastavenie zákazníka chýba";

function matrix(row: Partial<DeployMatrixRow> = {}): DeployMatrix {
  return {
    project_slug: "nex-websites",
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
        uat_version: "0.1.0",
        prod_version: "0.1.0",
        uat_last_attempt_failed: false,
        prod_last_attempt_failed: false,
        uat_last_deploy_at: null,
        uat_last_deploy_detail: null,
        uat_last_deploy_warnings: [],
        prod_last_deploy_at: null,
        prod_last_deploy_detail: null,
        prod_last_deploy_warnings: [],
        accepted_versions: ["0.2.0"],
        uat_url: null,
        prod_url: null,
        ...row,
      },
    ],
  };
}

/** The row after a successful deploy of 0.2.0 to ``env``, as the server reads it back. */
function deployed(env: DeployEnvironment, warnings: string[]): Partial<DeployMatrixRow> {
  return env === "uat"
    ? { uat_version: "0.2.0", uat_last_deploy_at: "2026-10-15T08:00:00Z", uat_last_deploy_detail: "OK", uat_last_deploy_warnings: warnings }
    : { prod_version: "0.2.0", prod_last_deploy_at: "2026-10-15T08:00:00Z", prod_last_deploy_detail: "OK", prod_last_deploy_warnings: warnings };
}

function answer(env: DeployEnvironment, ok: boolean, warnings: string[]): DeployResult {
  return {
    ok,
    event: {
      id: "evt-1",
      seq: 7,
      customer_id: "cust-1",
      project_id: "proj-1",
      version_number: "0.2.0",
      environment: env,
      event_type: "deploy",
      status: ok ? "ok" : "failed",
      actor_id: "user-1",
      detail: ok ? "OK" : "kontajner nenabehol",
      warnings,
      created_at: "2026-10-15T08:00:00Z",
      updated_at: "2026-10-15T08:00:00Z",
    },
    url: null,
    bumped_to: null,
    warnings,
  };
}

function said(text: string): number {
  return screen.queryAllByText(new RegExp(text)).length;
}

beforeEach(() => {
  vi.mocked(getDeployMatrix).mockReset();
  vi.mocked(deployCustomer).mockReset();
});

describe("DEV-129 — každé upozornenie nasadenia raz", () => {
  it.each(["uat", "prod"] as const)("after a successful deploy to %s each warning is on the page once", async (env) => {
    vi.mocked(getDeployMatrix)
      .mockResolvedValueOnce(matrix())
      .mockResolvedValue(matrix(deployed(env, [LAUNCH, HEALTH])));
    vi.mocked(deployCustomer).mockResolvedValueOnce(answer(env, true, [LAUNCH, HEALTH]));

    render(<DeployMatrixPage environment={env} />);
    fireEvent.click(await screen.findByRole("button", { name: /^Nasadiť$/ }));

    // The row has read the deploy back from the server — from here on both places could say the warnings.
    await screen.findByText(/^nasadené /);
    await waitFor(() => expect(said(LAUNCH)).toBe(1));
    expect(said(HEALTH)).toBe(1);
    expect(screen.queryByText(/warnings/i)).not.toBeInTheDocument();
  });

  it("after a refresh the row says the last deploy's warnings, each on its own line", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix(deployed("prod", [LAUNCH, HEALTH])));

    render(<DeployMatrixPage environment="prod" />);

    expect(await screen.findByText(new RegExp(LAUNCH))).toBeInTheDocument();
    expect(screen.getByText(new RegExp(HEALTH))).not.toBe(screen.getByText(new RegExp(LAUNCH)));
  });

  it("a failed deploy says its own warnings once, beside the last good deploy's", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix({ ...deployed("uat", [OLD]), uat_version: "0.1.0" }));
    vi.mocked(deployCustomer).mockResolvedValueOnce(answer("uat", false, [FAILED]));

    render(<DeployMatrixPage environment="uat" />);
    fireEvent.click(await screen.findByRole("button", { name: /^Nasadiť$/ }));

    await screen.findByText(/Nasadenie zlyhalo/);
    expect(said(FAILED)).toBe(1);
    expect(said(OLD)).toBe(1);
  });
});

/**
 * DeployMatrixPage — a FAILED deploy must not render as a green success.
 *
 * `POST /customers/{id}/deploy` answers HTTP 200 with `ok: false` for every RUNTIME failure: the build
 * failed, the container never came up, '/api' did not respond, a migration failed, the public PROD route is
 * down, the launch ticket could not be minted. Nothing throws, so `handleDeploy`'s catch never runs — and the
 * result block was gated only on "a result exists", so it painted "✓ Nasadené" over a deploy that did not
 * happen. The live audit-log already holds such an event ("backend '/api' not responding within 120s").
 *
 * These pin the branch: the failure reason (`event.detail`, non-secret by contract) is on screen and the
 * success block is gone — and the success path still renders exactly as before.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import DeployMatrixPage from "@/components/deploy/DeployMatrixPage";
import { deployCustomer, getDeployMatrix } from "@/services/api/deploy";
import type { DeployEventRead, DeployMatrix, DeployResult } from "@/types/deploy";

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

// A project with a deployable version and no block — so "Nasadiť" is live and the deploy actually fires.
function deployableMatrix(): DeployMatrix {
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
        prod_version: null,
        uat_last_attempt_failed: false,
        prod_last_attempt_failed: false,
        accepted_versions: ["0.2.0"],
        uat_url: "https://uat-icc-nex-websites.isnex.eu",
        prod_url: null,
      },
    ],
  };
}

function event(over: Partial<DeployEventRead> = {}): DeployEventRead {
  return {
    id: "evt-1",
    seq: 42,
    customer_id: "cust-1",
    project_id: "proj-1",
    version_number: "0.2.0",
    environment: "uat",
    event_type: "deploy",
    status: "ok",
    actor_id: "user-1",
    detail: null,
    created_at: "2026-07-26T10:00:00Z",
    updated_at: "2026-07-26T10:02:00Z",
    ...over,
  };
}

function result(over: Partial<DeployResult> = {}): DeployResult {
  return { ok: true, event: event(), url: null, bumped_to: null, warnings: [], ...over };
}

describe("DeployMatrixPage — a failed deploy never reads as a success", () => {
  beforeEach(() => {
    vi.mocked(getDeployMatrix).mockReset();
    vi.mocked(deployCustomer).mockReset();
    vi.mocked(getDeployMatrix).mockResolvedValue(deployableMatrix());
  });

  it("shows the failure and its reason when the backend answers 200 with ok:false", async () => {
    vi.mocked(deployCustomer).mockResolvedValueOnce(
      result({
        ok: false,
        url: null,
        event: event({ status: "failed", detail: "backend '/api' not responding within 120s" }),
      }),
    );

    render(<DeployMatrixPage environment="uat" />);
    fireEvent.click(await screen.findByRole("button", { name: /Nasadiť/ }));

    await waitFor(() => expect(screen.getByText(/Nasadenie zlyhalo/)).toBeInTheDocument());
    // The backend's own non-secret reason, verbatim — not a generic "skús znova".
    expect(screen.getByText(/not responding within 120s/)).toBeInTheDocument();
    // …and the green claim is GONE, not merely contradicted lower down.
    expect(screen.queryByText(/✓ Nasadené/)).not.toBeInTheDocument();
  });

  it("still reports failure when the backend gives no detail", async () => {
    vi.mocked(deployCustomer).mockResolvedValueOnce(
      result({ ok: false, event: event({ status: "failed", detail: null }) }),
    );

    render(<DeployMatrixPage environment="uat" />);
    fireEvent.click(await screen.findByRole("button", { name: /Nasadiť/ }));

    await waitFor(() => expect(screen.getByText(/Nasadenie zlyhalo/)).toBeInTheDocument());
    expect(screen.queryByText(/✓ Nasadené/)).not.toBeInTheDocument();
  });

  it("keeps the success confirmation (link + version bump) for ok:true", async () => {
    vi.mocked(deployCustomer).mockResolvedValueOnce(
      result({
        ok: true,
        url: "https://uat-icc-nex-websites.isnex.eu",
        bumped_to: "1.0.0",
        warnings: ["migrácie preskočené"],
      }),
    );

    render(<DeployMatrixPage environment="uat" />);
    fireEvent.click(await screen.findByRole("button", { name: /Nasadiť/ }));

    await waitFor(() => expect(screen.getByText(/✓ Nasadené/)).toBeInTheDocument());
    expect(screen.getByText(/projekt povýšený na/)).toBeInTheDocument();
    // Two links now: the row's persistent one and the fresh post-deploy one.
    expect(screen.getAllByRole("link", { name: /Otvoriť aplikáciu/ })).toHaveLength(2);
    expect(screen.getByText(/migrácie preskočené/)).toBeInTheDocument();
    expect(screen.queryByText(/Nasadenie zlyhalo/)).not.toBeInTheDocument();
  });
});

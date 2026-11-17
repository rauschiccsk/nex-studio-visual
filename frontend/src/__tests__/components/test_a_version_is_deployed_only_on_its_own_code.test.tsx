/**
 * DEV-416 — 17.11.2026, NEX Manager: customers ran 1.6.0, yet „Nasadiť verziu“ offered and preselected the released
 * 1.2.2, and a deploy would have built the code of the 1.7.0 in progress under that number. The backend now offers a
 * version only on its own code; the screen says why a finished version is missing (`superseded`), never goes back to
 * an older version by default, and the version's own screen does not call it ready to deploy. Director: „súhlasím“.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render as rtlRender, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import DeployBlockNotice from "@/components/deploy/DeployBlockNotice";
import DeployMatrixPage from "@/components/deploy/DeployMatrixPage";
import HonestStatusStrip from "@/components/controlCenter/HonestStatusStrip";
import TaskPlanRail from "@/components/controlCenter/TaskPlanRail";
import { isOlderVersion } from "@/components/deploy/version";
import { getDeployMatrix } from "@/services/api/deploy";
import type { PipelineBoard, PipelineState } from "@/services/api/pipeline";
import type { DeployMatrix } from "@/types/deploy";

const { navigateMock } = vi.hoisted(() => ({ navigateMock: vi.fn() }));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock };
});
vi.mock("@/services/api/deploy", () => ({
  getDeployMatrix: vi.fn(),
  deployCustomer: vi.fn(),
  acceptCustomerUat: vi.fn(),
  uatLaunch: vi.fn(),
  getDeployProgress: vi.fn(() => Promise.resolve({ bezi: false, krok: null, trva_sekund: 0, krok_trva_sekund: 0 })),
  getUatGuide: vi.fn(() => new Promise(() => {})),
  getUatChecks: vi.fn(() => Promise.reject(new Error("not in this test"))),
}));

vi.mock("@/services/api/versions", () => ({
  getTaskPlan: vi.fn(() => Promise.resolve({ plan: [], epic_count: 0, feat_count: 0, task_count: 0 })),
}));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: MemoryRouter });

const contextMock = { selectedProject: { slug: "nex-manager", name: "NEX Manager" } };
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));

function row(customer: string, uatVersion: string | null) {
  return {
    customer_id: `cust-${customer}`,
    customer_name: customer,
    customer_slug: customer,
    subdomain: customer,
    uat_version: uatVersion,
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
    uat_url: null,
    prod_url: null,
  };
}

function matrix(rows: ReturnType<typeof row>[]): DeployMatrix {
  return {
    project_slug: "nex-manager",
    auth_mode: "password",
    verified_versions: ["1.2.2"],
    deployability: { cause: "ok", version_number: null, version_id: null, can_reverify: false },
    can_accept: false,
    can_deploy_prod: false,
    can_deploy: true,
    rows,
  } as DeployMatrix;
}

describe("DeployMatrixPage — never back to an older version by default (DEV-416)", () => {
  beforeEach(() => vi.mocked(getDeployMatrix).mockReset());

  it("a customer on a newer version gets no preselected older one, and Nasadiť says why it waits", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix([row("skit", "1.6.0")]));
    render(<DeployMatrixPage environment="uat" />);

    const picker = await screen.findByRole("combobox", { name: "Verzia na nasadenie — skit" });
    expect(picker).toHaveValue("");
    expect(screen.getByRole("option", { name: "vyber verziu" })).toBeDisabled();
    expect(screen.getByText("beží novšia — vyber verziu")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Nasadiť/ })).toBeDisabled();

    fireEvent.change(picker, { target: { value: "1.2.2" } });
    await waitFor(() => expect(screen.getByRole("button", { name: /Nasadiť/ })).toBeEnabled());
  });

  it("a customer on an older version gets the newest verified one, as before", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix([row("icc", "1.0.0")]));
    render(<DeployMatrixPage environment="uat" />);

    expect(await screen.findByRole("combobox", { name: "Verzia na nasadenie — icc" })).toHaveValue("1.2.2");
    expect(screen.getByRole("button", { name: /Nasadiť/ })).toBeEnabled();
  });
});

describe("DeployBlockNotice — a finished version whose code holds a newer version's work (DEV-416)", () => {
  beforeEach(() => navigateMock.mockReset());

  it("names the newer version, offers no re-check and leads there", () => {
    render(
      <DeployBlockNotice
        block={{
          cause: "superseded",
          version_number: "1.6.0",
          version_id: "ver-160",
          can_reverify: false,
          next_version_id: "ver-170",
          next_version_number: "1.7.0",
        }}
        projectSlug="nex-manager"
        onReverifyStarted={vi.fn()}
      />,
    );

    expect(screen.getByText("Nasadí sa až verzia 1.7.0")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Verzia 1.6.0 je hotová, ale v projekte už pribudla práca na verzii 1.7.0. To, čo by sa teraz nasadilo, " +
          "by bol jej kód pod číslom 1.6.0 — preto 1.6.0 k zákazníkovi nepúšťame. Nasadiť sa bude dať verzia 1.7.0, " +
          "keď bude hotová.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Over znova/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Otvoriť verziu 1.7.0" }));
    expect(navigateMock).toHaveBeenCalledWith("/projects/nex-manager/versions/ver-170");
  });
});

describe("HonestStatusStrip — a superseded version is not ready to deploy (DEV-416)", () => {
  it("reads amber: the newer version is the one that will be deployed", () => {
    render(
      <HonestStatusStrip
        state={{ current_stage: "done", status: "done" } as PipelineState}
        projectName="NEX Manager"
        versionNumber="1.6.0"
        reconnecting={false}
        error={null}
        verifiedProvenance="superseded"
      />,
    );

    expect(screen.getByText("Hotovo — nasadí sa už novšia verzia")).toBeInTheDocument();
  });
});

describe("TaskPlanRail — a superseded version's card does not lead to a deploy (DEV-416)", () => {
  it("says the newer version will be deployed and closes „Prejsť na nasadenie“", async () => {
    const board = {
      state: { current_stage: "done", status: "done", id: "s1", version_id: "v1" },
      recent_messages: [],
      available_actions: [],
      verified_provenance: "superseded",
    } as unknown as PipelineBoard;
    render(<TaskPlanRail versionId="v1" messages={[]} board={board} onBoard={() => {}} ci={null} />);

    expect(
      await screen.findByText(
        "V projekte už pribudla práca na novšej verzii — túto verziu už nasadiť ani znova overiť nejde. " +
          "K zákazníkovi pôjde novšia verzia, keď bude hotová.",
        { normalizer: (text) => text.replace(/\s+/g, " ").trim() },
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Prejsť na nasadenie/ })).toBeDisabled();
    expect(screen.queryByText(/pripravená na nasadenie/)).not.toBeInTheDocument();
  });
});

describe("isOlderVersion — the cockpit's one order of versions", () => {
  it.each([
    ["1.2.2", "1.6.0", true],
    ["1.6.0", "1.2.2", false],
    ["1.9.0", "1.10.0", true],
    ["v1.0.0", "1.0.0", false],
    ["1.6", "1.6.0", false],
    ["1.6.0", "1.6.1", true],
  ])("%s older than %s → %s", (a, b, older) => {
    expect(isOlderVersion(a, b)).toBe(older);
  });
});

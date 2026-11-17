/**
 * DEV-416 + DEV-420 — 17.11.2026, NEX Manager: customers ran 1.6.0, yet „Nasadiť verziu“ offered and preselected the
 * released 1.2.2, and a deploy would have built the code of the 1.7.0 in progress under that number. Since DEV-420 a
 * deploy builds the version's own verified commit (Director: „Súhlasím“): the screen offers every finished version,
 * never preselects one older than the installation runs, refuses it on PROD, allows it on UAT only built anew, and the
 * version's own screen says the verified state is what goes out.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render as rtlRender, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import DeployMatrixPage from "@/components/deploy/DeployMatrixPage";
import HonestStatusStrip from "@/components/controlCenter/HonestStatusStrip";
import TaskPlanRail from "@/components/controlCenter/TaskPlanRail";
import { getDeployMatrix } from "@/services/api/deploy";
import type { PipelineBoard, PipelineState } from "@/services/api/pipeline";
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

function row(customer: string, running: string | null, older: string[] = [], environment: "uat" | "prod" = "uat") {
  return {
    customer_id: `cust-${customer}`,
    customer_name: customer,
    customer_slug: customer,
    subdomain: customer,
    uat_version: running,
    prod_version: running,
    uat_last_attempt_failed: false,
    prod_last_attempt_failed: false,
    uat_last_deploy_at: null,
    uat_last_deploy_detail: null,
    uat_last_deploy_warnings: [],
    prod_last_deploy_at: null,
    prod_last_deploy_detail: null,
    prod_last_deploy_warnings: [],
    accepted_versions: ["1.6.0", "1.2.2"],
    // Only the tab's own list says what is older — the other one is empty, so reading the wrong one shows.
    uat_older_versions: environment === "uat" ? older : [],
    prod_older_versions: environment === "prod" ? older : [],
    uat_url: null,
    prod_url: null,
  };
}

function matrix(rows: ReturnType<typeof row>[], verified = ["1.6.0", "1.2.2"]): DeployMatrix {
  return {
    project_slug: "nex-manager",
    auth_mode: "password",
    verified_versions: verified,
    deployability: { cause: "ok", version_number: null, version_id: null, can_reverify: false },
    can_accept: true,
    can_deploy_prod: true,
    can_deploy: true,
    rows,
  } as DeployMatrix;
}

const picker = (customer: string) => screen.findByRole("combobox", { name: `Verzia na nasadenie — ${customer}` });

describe("DeployMatrixPage — every finished version, never back to an older one by default (DEV-416, DEV-420)", () => {
  beforeEach(() => vi.mocked(getDeployMatrix).mockReset());

  it("while 1.7.0 is in progress, 1.6.0 is offered and preselected for a customer on 1.6.0", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix([row("skit", "1.6.0", ["1.2.2"])]));
    render(<DeployMatrixPage environment="uat" />);

    const versions = await picker("skit");
    expect(versions).toHaveValue("1.6.0");
    expect(within(versions).getAllByRole("option").map((o) => o.textContent)).toEqual(["1.6.0", "1.2.2"]);
    expect(screen.getByRole("button", { name: /Nasadiť/ })).toBeEnabled();
  });

  it("a customer on a version newer than any offered gets no preselected older one", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix([row("icc", "1.7.0", ["1.6.0", "1.2.2"])]));
    render(<DeployMatrixPage environment="uat" />);

    expect(await picker("icc")).toHaveValue("");
    expect(within(await picker("icc")).getByRole("option", { name: "vyber verziu" })).toBeDisabled();
    expect(screen.getByText("beží novšia verzia")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Nasadiť/ })).toBeDisabled();
  });

  it("on UAT an older version goes only through „Postaviť nanovo“", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix([row("skit", "1.6.0", ["1.2.2"])]));
    render(<DeployMatrixPage environment="uat" />);

    fireEvent.change(await picker("skit"), { target: { value: "1.2.2" } });

    await waitFor(() => expect(screen.getByRole("button", { name: /Nasadiť/ })).toBeDisabled());
    expect(screen.getByRole("button", { name: /Nasadiť/ })).toHaveAttribute(
      "title",
      "Na UAT beží novšia verzia 1.6.0. Staršia verzia 1.2.2 nepozná jej databázu — nasadíš ju len cez „Postaviť " +
        "nanovo“, ktoré údaje UAT zmaže.",
    );
    expect(screen.getByText("staršia verzia — len „Postaviť nanovo“")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Postaviť nanovo/ })).toBeEnabled();
  });

  it("on PROD an older version is never deployed", async () => {
    vi.mocked(getDeployMatrix).mockResolvedValue(matrix([row("skit", "1.6.0", ["1.2.2"], "prod")]));
    render(<DeployMatrixPage environment="prod" />);

    fireEvent.change(await picker("skit"), { target: { value: "1.2.2" } });

    await waitFor(() => expect(screen.getByRole("button", { name: /Nasadiť/ })).toBeDisabled());
    expect(screen.getByRole("button", { name: /Nasadiť/ })).toHaveAttribute(
      "title",
      "U zákazníka beží novšia verzia 1.6.0. Staršia verzia 1.2.2 nepozná jej databázu a nenaštartovala by — návrat na " +
        "staršiu verziu sa na PROD nerobí.",
    );
    expect(screen.getByText("beží novšia verzia")).toBeInTheDocument();
  });
});

describe("the version's own screen — a drifted or superseded version deploys its verified state (DEV-420)", () => {
  it("the strip says a newer version has begun, amber", () => {
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

    expect(screen.getByText("Hotovo — v projekte už pribudla novšia verzia")).toBeInTheDocument();
  });

  it("the card says it cannot be checked again, and leads to the deploy of its verified code", async () => {
    const board = {
      state: { current_stage: "done", status: "done", id: "s1", version_id: "v1" },
      recent_messages: [],
      available_actions: [],
      verified_provenance: "superseded",
    } as unknown as PipelineBoard;
    render(<TaskPlanRail versionId="v1" messages={[]} board={board} onBoard={() => {}} ci={null} />);

    expect(
      await screen.findByText(
        "V projekte už pribudla práca na novšej verzii — túto verziu už znova overiť nejde. Nasadiť sa dá s kódom, " +
          "s ktorým bola overená.",
        { normalizer: (text) => text.replace(/\s+/g, " ").trim() },
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Prejsť na nasadenie/ })).toBeEnabled();
  });
});

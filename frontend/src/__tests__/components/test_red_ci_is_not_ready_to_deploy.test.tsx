/**
 * DEV-51 — a finished version whose checks (CI) failed is never "pripravená na nasadenie".
 *
 * 10.10.2026, NEX Inbox 1.7.0: the release gate the version tag started failed ten minutes after the Verifikácia
 * gate had said "CI zelené". Riadiace centrum then showed "Zostavenie zlyhalo" in one line and
 * "Hotovo — pripravené na nasadenie" with a live "Prejsť na nasadenie" right below it. The backend now closes the
 * deploy (deploy.ci_deploy_cause); these pin that the screen says the same thing, from one CI read.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render as rtlRender, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import HonestStatusStrip from "@/components/riadiace/HonestStatusStrip";
import { PlanUlohRail } from "@/components/riadiace/PlanUlohRail";
import DeployBlockNotice from "@/components/deploy/DeployBlockNotice";
import { getTaskPlan } from "@/services/api/versions";
import type { CiStatus, PipelineBoard, PipelineState } from "@/services/api/pipeline";
import type { DeployBlock } from "@/types/deploy";

const { navigateMock } = vi.hoisted(() => ({ navigateMock: vi.fn() }));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock };
});
vi.mock("@/services/api/versions", () => ({ getTaskPlan: vi.fn() }));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: MemoryRouter });

const RUN = "https://github.com/rauschiccsk/nex-inbox/actions/runs/38042619283";
const RED: CiStatus = {
  stav: "red",
  detail: "CI zlyhalo (postup Release smoke gate, beh 38042619283, failure)",
  sha: "48de080",
  url: RUN,
};
const RUNNING: CiStatus = {
  stav: "unknown",
  detail: "CI ešte beží (postup Release smoke gate, beh 38042619283)",
  sha: "48de080",
  bezi: true,
  url: RUN,
};
const GREEN: CiStatus = { stav: "green", detail: "CI zelené (postup CI, beh 5)", sha: "48de080" };
const DONE = { current_stage: "done", status: "done", mode: "conversation" } as PipelineState;

function strip(ci: CiStatus | null, verifiedProvenance: string | null = "hotovo_match") {
  return (
    <HonestStatusStrip
      state={DONE}
      projectName="NEX Inbox"
      versionNumber="1.7.0"
      reconnecting={false}
      error={null}
      verifiedProvenance={verifiedProvenance}
      ci={ci}
    />
  );
}

describe("pruh stavu pri hotovej verzii", () => {
  it("⚠️ pri červených kontrolách nepovie „pripravené na nasadenie“", () => {
    render(strip(RED));

    expect(screen.getByText(/kontroly projektu zlyhali, nasadenie je zastavené/)).toBeInTheDocument();
    expect(screen.queryByText(/pripravené na nasadenie/)).not.toBeInTheDocument();
  });

  it("pri bežiacich kontrolách povie, že nasadenie počká", () => {
    render(strip(RUNNING));

    expect(screen.getByText(/kontroly projektu ešte bežia, nasadenie počká/)).toBeInTheDocument();
  });

  it.each([GREEN, null])("pri zelených alebo neznámych kontrolách je verzia pripravená (%#)", (ci) => {
    render(strip(ci));

    expect(screen.getByText("Hotovo — pripravené na nasadenie")).toBeInTheDocument();
  });

  it("zmenený kód má prednosť — rieši sa najprv znovuoverenie", () => {
    render(strip(RED, "hotovo_drift"));

    expect(screen.getByText(/kód sa odvtedy zmenil/)).toBeInTheDocument();
  });
});

function rail(ci: CiStatus | null) {
  const board = {
    state: { ...DONE, id: "s1", version_id: "v1" },
    recent_messages: [],
    available_actions: [],
    verified_provenance: "hotovo_match",
  } as unknown as PipelineBoard;
  return <PlanUlohRail versionId="v1" messages={[]} board={board} onBoard={() => {}} ci={ci} />;
}

describe("pravý panel pri hotovej verzii", () => {
  beforeEach(() => {
    vi.mocked(getTaskPlan).mockResolvedValue({ plan: [], epic_count: 0, feat_count: 0, task_count: 0 });
  });

  it("⚠️ pri červených kontrolách nesľúbi nasadenie", async () => {
    render(rail(RED));

    expect(await screen.findByText(/Kontroly projektu na kóde, ktorý by sa nasadil, zlyhali/)).toBeInTheDocument();
    expect(screen.queryByText(/pripravená na nasadenie/)).not.toBeInTheDocument();
  });

  it("pri bežiacich kontrolách povie, že sa nasadenie odomkne", async () => {
    render(rail(RUNNING));

    expect(await screen.findByText(/Kontroly projektu ešte bežia/)).toBeInTheDocument();
    expect(screen.queryByText(/pripravená na nasadenie/)).not.toBeInTheDocument();
  });

  it("pri zelených kontrolách je verzia pripravená", async () => {
    render(rail(GREEN));

    expect(await screen.findByText(/Verzia je hotová a pripravená na nasadenie/)).toBeInTheDocument();
  });
});

function block(cause: DeployBlock["cause"], ci: CiStatus, fix: Partial<DeployBlock> = {}): DeployBlock {
  return {
    cause,
    version_number: "1.7.0",
    version_id: "ver-17",
    can_reverify: false,
    ci_detail: ci.detail,
    ci_url: ci.url,
    ...fix,
  };
}

describe("obrazovka nasadenia povie, prečo je „Nasadiť“ zavreté", () => {
  beforeEach(() => navigateMock.mockReset());

  it("⚠️ zlyhané kontroly: ktorá a kde ju vidieť, a cesta k oprave", async () => {
    render(<DeployBlockNotice block={block("ci_red", RED)} projectSlug="nex-inbox" onReverifyStarted={vi.fn()} />);

    expect(screen.getByText(/Nasadenie je zastavené — kontroly projektu zlyhali/)).toBeInTheDocument();
    expect(screen.getByText(/postup Release smoke gate, beh 38042619283/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Pozrieť beh na GitHube/ })).toHaveAttribute("href", RUN);

    // DEV-57: the way on is the fix, not the finished version — with nothing begun, start a fast fix.
    expect(screen.queryByRole("button", { name: /Otvoriť verziu/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Spustiť rýchlu opravu" }));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/projects/nex-inbox?rychla-oprava=1"));
  });

  it("⚠️ zlyhané kontroly a oprava už beží: vedie k nej, druhú neponúkne (DEV-57)", async () => {
    render(
      <DeployBlockNotice
        block={block("ci_red", RED, { next_version_id: "ver-171", next_version_number: "1.7.1", dedo_brief: "fast_fix" })}
        projectSlug="nex-inbox"
        onReverifyStarted={vi.fn()}
      />,
    );

    expect(screen.getByText(/Oprava je už rozpracovaná vo verzii 1\.7\.1/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Spustiť rýchlu opravu" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Otvoriť verziu 1.7.1" }));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/projects/nex-inbox/versions/ver-171"));
  });

  it("⚠️ zlyhané kontroly a čaká Dedovo zadanie: vedie k nemu (DEV-57)", async () => {
    render(
      <DeployBlockNotice
        block={block("ci_red", RED, { dedo_brief: "fast_fix" })}
        projectSlug="nex-inbox"
        onReverifyStarted={vi.fn()}
      />,
    );

    expect(screen.getByText(/Dedo \(náš technický tím\) pripravil zadanie opravy/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Otvoriť zadanie od Deda" }));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/projects/nex-inbox#zadanie-od-deda"));
  });

  it("bežiace kontroly: počká sa, nič netreba stláčať", () => {
    render(
      <DeployBlockNotice block={block("ci_running", RUNNING)} projectSlug="nex-inbox" onReverifyStarted={vi.fn()} />,
    );

    expect(screen.getByText("Kontroly projektu ešte bežia")).toBeInTheDocument();
    expect(screen.getByText(/obnovuje sama/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

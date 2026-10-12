/**
 * DEV-82 — on a computer the task plan is as wide as the person left it, kept on the server; its left edge is the
 * handle (moving it right narrows the plan), within the bounds.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector: (s: unknown) => unknown) => selector({ user: { role: "ri" } }),
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: unknown) => unknown) =>
    selector({
      selectedProject: { slug: "nex-inbox", name: "NEX Inbox" },
      selectedVersion: { versionId: "v-18", versionNumber: "1.8.0" },
    }),
}));
vi.mock("@/hooks/usePipelineWs", () => ({
  usePipelineWs: () => ({
    board: { state: { current_stage: "programovanie", status: "agent_working" }, recent_messages: [], available_actions: [] },
    activity: [],
    reconnecting: false,
    error: null,
    accessDenied: false,
    setBoard: vi.fn(),
  }),
}));
vi.mock("@/services/api/pipeline", () => ({
  relayPipelineMessageApi: vi.fn(),
  postPipelineActionApi: vi.fn(),
  getCiStatusApi: vi.fn().mockResolvedValue(null),
}));
vi.mock("@/services/api/userLayout", () => ({ getUserLayout: vi.fn(), saveUserLayout: vi.fn() }));
vi.mock("@/components/riadiace/ConversationComposer", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/ConversationThread", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/SpecApprovalBar", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/PhaseBar", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/StavZostavenia", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/HonestStatusStrip", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/PlanUlohRail", () => ({ default: () => <aside data-testid="plan" /> }));

import RiadiaceCentrumPage from "@/pages/RiadiaceCentrumPage";
import { saveUserLayout } from "@/services/api/userLayout";
import { useUserLayoutStore } from "@/store/userLayoutStore";

const layout = (over: object = {}) => ({
  sidebar_width: null,
  sidebar_collapsed: false,
  plan_width: null,
  limits: { sidebar_width: { min: 180, max: 360, default: 224 }, plan_width: { min: 280, max: 640, default: 320 } },
  ...over,
});

const grid = () => screen.getByTestId("plan").parentElement!.parentElement as HTMLElement;
const handle = () => screen.getByRole("separator", { name: "Šírka Plánu úloh" });

beforeEach(() => {
  vi.mocked(saveUserLayout)
    .mockReset()
    .mockImplementation(async (changes) => layout({ ...useUserLayoutStore.getState().layout, ...changes }));
});

describe("the task plan's width (DEV-82)", () => {
  it("is the width kept on the server", () => {
    useUserLayoutStore.setState({ layout: layout({ plan_width: 480 }) });
    render(<RiadiaceCentrumPage />);

    expect(grid().style.gridTemplateColumns).toBe("minmax(0,1fr) 480px");
    expect(handle()).toHaveAttribute("aria-valuenow", "480");
  });

  it("moving the edge right narrows the plan, within the bounds, and the change is sent", async () => {
    useUserLayoutStore.setState({ layout: layout() });
    render(<RiadiaceCentrumPage />);

    fireEvent.keyDown(handle(), { key: "ArrowRight" });
    await waitFor(() => expect(saveUserLayout).toHaveBeenLastCalledWith({ plan_width: 304 }));
    expect(grid().style.gridTemplateColumns).toBe("minmax(0,1fr) 304px");

    fireEvent.keyDown(handle(), { key: "Home" });
    await waitFor(() => expect(saveUserLayout).toHaveBeenLastCalledWith({ plan_width: 280 }));
    fireEvent.doubleClick(handle());
    await waitFor(() => expect(saveUserLayout).toHaveBeenLastCalledWith({ plan_width: null }));
  });

  it("before the layout arrives the column keeps its default and there is no handle", () => {
    useUserLayoutStore.setState({ layout: null });
    render(<RiadiaceCentrumPage />);

    expect(grid().style.gridTemplateColumns).toBe("");
    expect(screen.queryByRole("separator", { name: "Šírka Plánu úloh" })).toBeNull();
  });
});

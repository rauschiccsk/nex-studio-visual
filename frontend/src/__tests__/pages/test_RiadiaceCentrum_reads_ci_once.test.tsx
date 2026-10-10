/**
 * DEV-51 — Riadiace centrum reads the build's CI ONCE and hands the same answer to every place that shows it.
 *
 * 10.10.2026, NEX Inbox 1.7.0: the line by the phases said "Zostavenie zlyhalo" while the status strip and the
 * rail said "pripravené na nasadenie". Each piece read (or ignored) CI on its own; from one read they cannot
 * disagree.
 */

import { describe, it, expect, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import RiadiaceCentrumPage from "@/pages/RiadiaceCentrumPage";
import { getCiStatusApi } from "@/services/api/pipeline";

const { seen, red } = vi.hoisted(() => ({
  seen: { strip: [] as unknown[], rail: [] as unknown[], line: [] as unknown[] },
  red: { stav: "red", detail: "CI zlyhalo (postup Release smoke gate, beh 38042619283, failure)", sha: "48de080" },
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector: (s: unknown) => unknown) => selector({ user: { role: "ha" } }),
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: unknown) => unknown) =>
    selector({
      selectedProject: { slug: "nex-inbox", name: "NEX Inbox" },
      selectedVersion: { versionId: "v-17", versionNumber: "1.7.0" },
    }),
}));
vi.mock("@/hooks/usePipelineWs", () => ({
  usePipelineWs: () => ({
    board: { state: { current_stage: "done", status: "done" }, recent_messages: [], available_actions: [] },
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
  getCiStatusApi: vi.fn().mockResolvedValue(red),
}));
vi.mock("@/components/riadiace/ConversationComposer", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/ConversationThread", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/SpecApprovalBar", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/PhaseBar", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/StavZostavenia", () => ({
  default: (props: { stav: unknown }) => {
    seen.line.push(props.stav);
    return <div />;
  },
}));
vi.mock("@/components/riadiace/HonestStatusStrip", () => ({
  default: (props: { ci?: unknown }) => {
    seen.strip.push(props.ci);
    return <div />;
  },
}));
vi.mock("@/components/riadiace/PlanUlohRail", () => ({
  default: (props: { ci?: unknown }) => {
    seen.rail.push(props.ci);
    return <div />;
  },
}));

describe("Riadiace centrum — jeden stav CI pre celú stránku", () => {
  it("⚠️ riadok pri fázach, pruh stavu aj pravý panel dostanú tú istú odpoveď z jedného dopytu", async () => {
    render(<RiadiaceCentrumPage />);

    await waitFor(() => expect(seen.strip.at(-1)).toEqual(red));
    expect(seen.rail.at(-1)).toEqual(red);
    expect(seen.line.at(-1)).toEqual(red);
    expect(getCiStatusApi).toHaveBeenCalledTimes(1);
  });
});

/**
 * DEV-318 — the two ways an idea ends, under its latest brief: „Založiť projekt“ (only when the brief says who pays the
 * first version — the project's ticket carries it, DEV-118) and „Pridať do projektu“ (a requirement of its Zásobník).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({ ideaAddedApi: vi.fn() }));
vi.mock("@/services/api/ideas", () => api);

import IdeaOutcomeActions from "@/components/poradca/IdeaOutcomeActions";
import type { Idea, PoradcaMessage } from "@/types/poradca";

const IDEA = { id: "i1", title: "Matrika", state: "brief_ready" } as Idea;
const message = (extra: Partial<PoradcaMessage>) => ({ id: "m1", author: "poradca", status: "done", ...extra }) as PoradcaMessage;

beforeEach(() => api.ideaAddedApi.mockReset());

describe("IdeaOutcomeActions (DEV-318)", () => {
  it("a brief that does not say who pays founds nothing yet and says so", () => {
    render(
      <MemoryRouter>
        <IdeaOutcomeActions idea={IDEA} message={message({ idea_project: { name: "Orthodox Register", work_kind: null, brief: "x" } })} onEnded={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: /Založiť projekt/ })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("kto prvú verziu platí");
  });

  it("a brief for an existing project is added to its Zásobník with one click", async () => {
    const onEnded = vi.fn();
    api.ideaAddedApi.mockResolvedValue({ ...IDEA, state: "added", outcome_backlog_number: 7 });
    render(
      <MemoryRouter>
        <IdeaOutcomeActions idea={IDEA} message={message({ idea_addition: { project_slug: "nex-manager", title: "Skupiny" } })} onEnded={onEnded} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: /Pridať do projektu nex-manager/ }));
    await waitFor(() => expect(api.ideaAddedApi).toHaveBeenCalledWith("i1", "m1"));
    expect(onEnded).toHaveBeenCalledWith(expect.objectContaining({ state: "added", outcome_backlog_number: 7 }));
  });
});

/**
 * DEV-7 — a database change is approved by Ri with one click, and the screen says what is being approved.
 *
 * icc/SCHEMA_GOVERNANCE.md gives the approval of a database change to Ri alone. Until DEV-7 the agent asked in
 * free text, the Director answered in free text and Dedo copied the schema into the Knowledge Base by hand
 * (dedo-home, 02.10.2026); the Návrh approval never mentioned the database at all (NEX Inbox 1.7.0).
 *
 * Pinned: the Návrh approval says the Návrh changes the database and opens the schema; for anyone but Ri the
 * approval is greyed out with the reason. The Programovanie stop (`schema_approval`) offers „Schváliť štruktúru
 * databázy" — Ri only — which posts `schvalit_schemu`.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import SchvalitBar from "@/components/riadiace/SchvalitBar";
import SchemaApprovalBar from "@/components/riadiace/SchemaApprovalBar";
import { postPipelineActionApi, type DatabaseSchema, type PipelineBoard } from "@/services/api/pipeline";

const { navigateMock, who } = vi.hoisted(() => ({ navigateMock: vi.fn(), who: { role: "ri" as string } }));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock };
});
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ user: { id: "u1", username: "alex", role: who.role } }),
}));

const SCHEMA: DatabaseSchema = {
  path: "docs/specs/versions/v0.1.0/DATABASE_SCHEMAS.md",
  kb_path: "projects/career-asistent/DATABASE_SCHEMAS.md",
  kb_exists: true,
  changes: true,
  added_lines: 2,
  removed_lines: 1,
  approver_role: "ri",
};
const ONLY_RI = "Štruktúru databázy schvaľuje len Ri — tento krok schváli účet s rolou Ri.";
const CHANGE = "Oproti schválenej schéme v Znalostnej báze pribúda 2 a ubúda 1 riadkov.";
const DOC_LINK = "/specifikacia?doc=docs%2Fspecs%2Fversions%2Fv0.1.0%2FDATABASE_SCHEMAS.md";

function board(actions: string[], stage: string, schema: DatabaseSchema | null, nextAction = ""): PipelineBoard {
  return {
    state: { current_stage: stage, status: "blocked", next_action: nextAction },
    recent_messages: [],
    available_actions: actions,
    database_schema: schema,
  } as unknown as PipelineBoard;
}

beforeEach(() => {
  who.role = "ri";
  navigateMock.mockReset();
  vi.mocked(postPipelineActionApi).mockReset().mockResolvedValue(board([], "vizual", null));
});

describe("DEV-7 — the Návrh approval of a database change", () => {
  it("says the Návrh changes the database, how much, and opens the schema", () => {
    render(<SchvalitBar board={board(["schvalit", "uprav"], "navrh", SCHEMA)} versionId="v-1" onBoard={vi.fn()} />);

    expect(screen.getByText(new RegExp(`Návrh mení štruktúru databázy\\. ${CHANGE}`))).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Prezrieť štruktúru databázy" }));
    expect(navigateMock).toHaveBeenCalledWith(DOC_LINK);
    expect(screen.getByRole("button", { name: /Schváliť plán/ })).toBeEnabled();
    expect(screen.queryByText(ONLY_RI)).not.toBeInTheDocument();
  });

  it("is greyed out with the reason for anyone but Ri — the rework stays open", () => {
    who.role = "ha";
    render(<SchvalitBar board={board(["schvalit", "uprav"], "navrh", SCHEMA)} versionId="v-1" onBoard={vi.fn()} />);

    expect(screen.getByRole("button", { name: /Schváliť plán/ })).toBeDisabled();
    expect(screen.getByText(ONLY_RI)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Upraviť/ })).toBeEnabled();
  });

  it("a first schema says it is the first one", () => {
    render(
      <SchvalitBar
        board={board(["schvalit"], "navrh", { ...SCHEMA, kb_exists: false, added_lines: 6, removed_lines: 0 })}
        versionId="v-1"
        onBoard={vi.fn()}
      />,
    );

    expect(screen.getByText(/Je to prvá schéma databázy projektu — v Znalostnej báze ešte nie je\./)).toBeInTheDocument();
  });

  it("a Návrh without a database change is approved by anyone, as before", () => {
    who.role = "ha";
    render(
      <SchvalitBar board={board(["schvalit"], "navrh", { ...SCHEMA, changes: false })} versionId="v-1" onBoard={vi.fn()} />,
    );

    expect(screen.getByRole("button", { name: /Schváliť plán/ })).toBeEnabled();
    expect(screen.queryByText(/mení štruktúru databázy/)).not.toBeInTheDocument();
  });
});

describe("DEV-7 — the Programovanie stop for a database change", () => {
  const STOP = "AI Agent (úloha #3) potrebuje zmeniť štruktúru databázy: Pridať stĺpec email. Schváliť ju smie len Ri.";

  it("renders only while the backend offers the approval", () => {
    const { container } = render(
      <SchemaApprovalBar board={board(["answer"], "programovanie", SCHEMA)} versionId="v-1" onBoard={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("Ri approves with one click — it posts schvalit_schemu and adopts the returned board", async () => {
    const onBoard = vi.fn();
    render(
      <SchemaApprovalBar
        board={board(["schvalit_schemu", "answer"], "programovanie", SCHEMA, STOP)}
        versionId="v-1"
        onBoard={onBoard}
      />,
    );

    expect(screen.getByText(STOP)).toBeInTheDocument();
    expect(screen.getByText(CHANGE)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Schváliť štruktúru databázy" }));

    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledWith("v-1", { action: "schvalit_schemu" }));
    await waitFor(() => expect(onBoard).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Prezrieť štruktúru databázy" }));
    expect(navigateMock).toHaveBeenCalledWith(DOC_LINK);
  });

  it("anyone but Ri sees why the button is greyed out, and nothing is sent", () => {
    who.role = "shu";
    render(
      <SchemaApprovalBar board={board(["schvalit_schemu"], "programovanie", SCHEMA, STOP)} versionId="v-1" onBoard={vi.fn()} />,
    );

    const approve = screen.getByRole("button", { name: "Schváliť štruktúru databázy" });
    expect(approve).toBeDisabled();
    fireEvent.click(approve);
    expect(postPipelineActionApi).not.toHaveBeenCalled();
    expect(screen.getByText(ONLY_RI)).toBeInTheDocument();
  });
});

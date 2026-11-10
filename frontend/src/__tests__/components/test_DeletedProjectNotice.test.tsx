/**
 * DEV-342 — after „Zmazať natrvalo“ the list of projects says what the delete did: the idea back in Nápady, the GitHub
 * repository gone — and, as a warning that stays until closed, what stayed and needs a hand (before, a repository
 * GitHub refused to delete was written only to the server's log).
 */
import { afterEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";

import DeletedProjectNotice, { type DeletedProjectState } from "@/components/project/DeletedProjectNotice";

const IDEA = "Nápad „Orthodox Typikon“ je späť v Nápadoch aj s celým rozhovorom.";
const REPO_GONE = "Repozitár rauschiccsk/liturgikon na GitHube je zmazaný.";
const REPO_STAYED = "Repozitár rauschiccsk/liturgikon na GitHube ostal — GitHub zmazanie odmietol. Zmaž ho na GitHube ručne.";

function after(state?: DeletedProjectState) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/projects", state }]}>
      <DeletedProjectNotice />
    </MemoryRouter>,
  );
}

const notice = () => screen.queryByRole("status", { name: "Výsledok zmazania projektu" });

afterEach(() => window.history.replaceState({}, ""));

describe("DeletedProjectNotice (DEV-342)", () => {
  it("says the project is deleted and what was done", () => {
    after({ deletedProject: { name: "Liturgikon", notes: [IDEA, REPO_GONE], leftBehind: [] } });

    expect(notice()).toHaveTextContent("Projekt „Liturgikon“ je zmazaný.");
    expect(notice()).toHaveTextContent(IDEA);
    expect(notice()).toHaveTextContent(REPO_GONE);
    expect(notice()).not.toHaveTextContent("Čo ostalo a treba doriešiť");
  });

  it("what stayed is a warning of its own, never said as done", () => {
    after({ deletedProject: { name: "Liturgikon", notes: [IDEA], leftBehind: [REPO_STAYED] } });

    expect(notice()).toHaveTextContent("Čo ostalo a treba doriešiť");
    expect(notice()).toHaveTextContent(REPO_STAYED);
    expect(notice()?.className).toContain("warning");
  });

  it("everything done is shown as done", () => {
    after({ deletedProject: { name: "Liturgikon", notes: [IDEA], leftBehind: [] } });

    expect(notice()?.className).toContain("success");
  });

  it("closes on Zavrieť", () => {
    after({ deletedProject: { name: "Liturgikon", notes: [IDEA], leftBehind: [REPO_STAYED] } });

    fireEvent.click(screen.getByRole("button", { name: "Zavrieť" }));

    expect(notice()).toBeNull();
  });

  it("shows nothing without a delete", () => {
    after();

    expect(notice()).toBeNull();
  });
});

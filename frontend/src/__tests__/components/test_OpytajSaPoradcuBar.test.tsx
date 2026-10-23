/**
 * A finished version in the Riadiace centrum: instead of the message field, „Opýtaj sa Poradcu" (ICCINT-167) — which
 * asks Poradca about the version, as „Overiť s Poradcom" does on the cards (DEV-191). The whole path to the question
 * going out: test_FinishedVersionAsksPoradca.
 */

import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import OpytajSaPoradcuBar from "@/components/riadiace/OpytajSaPoradcuBar";
import { reviewQuestion, takeReview } from "@/lib/poradcaReview";

function Where() {
  const loc = useLocation();
  return <div>{`${loc.pathname}${loc.search}`}</div>;
}

describe("OpytajSaPoradcuBar", () => {
  it("hands the question about the finished version to Poradca and opens it on that version", () => {
    render(
      <MemoryRouter initialEntries={["/riadiace-centrum"]}>
        <Routes>
          <Route
            path="/riadiace-centrum"
            element={
              <OpytajSaPoradcuBar
                versionId="v11"
                projectName="NEX Inbox"
                versionNumber="1.10.1"
              />
            }
          />
          <Route path="/poradca" element={<Where />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(
      screen.getByText(/agent stavby na nej už nepracuje/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Opýtaj sa Poradcu/ }));

    expect(
      screen.getByText("/poradca?verzia=v11&overit=1"),
    ).toBeInTheDocument();
    const card = takeReview("v11");
    expect(card && reviewQuestion(card, 20_000)).toBe(
      "Verzia 1.10.1 projektu NEX Inbox je hotová. Zhrň mi, čo priniesla, čo mám vyskúšať v UAT a na čo si dať " +
        "pozor pred nasadením na PROD.",
    );
  });
});

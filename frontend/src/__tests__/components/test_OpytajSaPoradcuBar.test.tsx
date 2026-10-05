/** Hotová verzia v Riadiacom centre: namiesto poľa na správu „Opýtaj sa Poradcu" (ICCINT-167). */

import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import OpytajSaPoradcuBar from "@/components/riadiace/OpytajSaPoradcuBar";

function Where() {
  const loc = useLocation();
  return <div>{`${loc.pathname}${loc.search}`}</div>;
}

describe("OpytajSaPoradcuBar", () => {
  it("opens Poradca with the finished version preselected", () => {
    render(
      <MemoryRouter initialEntries={["/riadiace-centrum"]}>
        <Routes>
          <Route path="/riadiace-centrum" element={<OpytajSaPoradcuBar versionId="v11" />} />
          <Route path="/poradca" element={<Where />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText(/agent stavby na nej už nepracuje/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Opýtaj sa Poradcu/ }));
    expect(screen.getByText("/poradca?verzia=v11")).toBeInTheDocument();
  });
});

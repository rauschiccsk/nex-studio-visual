/**
 * DEV-88 — in a text rendered by the shared Markdown renderer, a file of the project is a link into NS Code at the line
 * it names — in the text, in inline code and in a code block (which still copies its plain text); without the list of
 * files nothing changes, and a consumer's own links keep working.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import { SpecMarkdown } from "@/components/markdown/SpecMarkdown";

const SHOWN = new Set(["backend/main.py", "README.md"]);

function Where() {
  const l = useLocation();
  return <p data-testid="where">{l.pathname + l.search}</p>;
}

function show(body: string, props: Partial<Parameters<typeof SpecMarkdown>[0]> = {}) {
  return render(
    <MemoryRouter initialEntries={["/riadiace-centrum"]}>
      <Routes>
        <Route
          path="/riadiace-centrum"
          element={<SpecMarkdown body={body} codePaths={SHOWN} projectSlug="nex-inbox" {...props} />}
        />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

const writeText = vi.fn();

beforeEach(() => {
  writeText.mockReset().mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText } });
});

describe("files named in a text are links into NS Code (DEV-88)", () => {
  it("a path in the text opens NS Code at its line, inside the app", () => {
    show("Chyba je v **backend/main.py:129**, nie v .env.");

    const link = screen.getByRole("link", { name: "backend/main.py:129" });
    expect(link).toHaveAttribute("href", "/ns-code?path=backend%2Fmain.py&line=129");
    expect(screen.queryByRole("link", { name: ".env" })).not.toBeInTheDocument();
    fireEvent.click(link);
    expect(screen.getByTestId("where")).toHaveTextContent("/ns-code?path=backend%2Fmain.py&line=129");
  });

  it("a path in inline code is a link too", () => {
    show("Otvor `README.md` a `backend/nie_je.py`.");

    expect(screen.getByRole("link", { name: "README.md" })).toHaveAttribute("href", "/ns-code?path=README.md");
    expect(screen.queryByRole("link", { name: "backend/nie_je.py" })).not.toBeInTheDocument();
  });

  it("a code block links its paths and still copies its plain text", async () => {
    show("```text\nbackend/main.py:12: error\nREADME.md\n```");

    expect(screen.getByRole("link", { name: "backend/main.py:12" })).toHaveAttribute(
      "href",
      "/ns-code?path=backend%2Fmain.py&line=12",
    );
    fireEvent.click(screen.getByRole("button", { name: /Kopírovať/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("backend/main.py:12: error\nREADME.md"));
  });

  it("a link the text already has stays one link, even when its text names a file", () => {
    show("Na GitHube: [backend/main.py](https://github.com/rauschiccsk/nex-inbox/blob/main/backend/main.py).");

    const links = screen.getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "https://github.com/rauschiccsk/nex-inbox/blob/main/backend/main.py",
    ]);
  });

  it("without the list of files the text stays as it was", () => {
    show("Chyba je v backend/main.py:129.", { codePaths: null });

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("a consumer's own link (Poradca's choice) is still drawn by the consumer", () => {
    show("Vyber [kartu 2](#volba-2) a pozri backend/main.py.", {
      components: { a: ({ href, children }) => <span data-testid="own">{`${href}|${String(children)}`}</span> },
    });

    expect(screen.getByTestId("own")).toHaveTextContent("#volba-2|kartu 2");
    expect(screen.getByRole("link", { name: "backend/main.py" })).toBeInTheDocument();
  });
});

/**
 * DEV-86 — NS Code: the tree of the selected project, a file opened from the tree, from the quick open (Ctrl+P), from a
 * search hit or from a link with a line; what cannot be shown says why. The editor itself is a stand-in here — the
 * editor of VS Code needs a real browser (the browser guard draws it).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import NsCodePage from "@/pages/NsCodePage";
import { ApiError } from "@/services/api";
import { getCodeFile, getCodeFiles, searchCode, type CodeFile, type CodeFiles } from "@/services/api/nsCode";

vi.mock("@/services/api/nsCode", () => ({
  getCodeFiles: vi.fn(),
  getCodeFile: vi.fn(),
  getCodeOriginal: vi.fn(),
  searchCode: vi.fn(),
}));
vi.mock("@/components/nscode/CodeEditor", () => ({
  default: ({ path, text, line }: { path: string; text: string; line: number | null }) => (
    <pre data-testid="editor" data-path={path} data-line={line ?? ""}>
      {text}
    </pre>
  ),
}));
vi.mock("@/contexts/ThemeContext", () => ({ useTheme: () => ({ isDark: true, toggleDark: vi.fn() }) }));

const context = { selectedProject: { slug: "nex-inbox", name: "NEX Inbox" } as { slug: string; name: string } | null };
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof context) => unknown) => selector(context),
}));

const FILES = ["README.md", "backend/main.py", "backend/apps/ubl.py", "frontend/src/App.tsx"];

function listing(over: Partial<CodeFiles> = {}): CodeFiles {
  return {
    head: "d626301458",
    files: FILES,
    changes: {},
    stamps: Object.fromEntries(FILES.map((f) => [f, "120-1"])),
    refresh_seconds: 10,
    ...over,
  };
}

function file(path: string, over: Partial<CodeFile> = {}): CodeFile {
  return {
    path,
    size: 120,
    text: `# ${path}\n`,
    binary: false,
    too_big: false,
    max_bytes: 1_000_000,
    stamp: "120-1",
    last_commit: null,
    ...over,
  };
}

function Where() {
  const l = useLocation();
  return <p data-testid="where">{l.pathname + l.search}</p>;
}

function page(address = "/ns-code") {
  return render(
    <MemoryRouter initialEntries={[address]}>
      <Routes>
        <Route
          path="/ns-code"
          element={
            <>
              <NsCodePage />
              <Where />
            </>
          }
        />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  context.selectedProject = { slug: "nex-inbox", name: "NEX Inbox" };
  vi.mocked(getCodeFiles).mockReset().mockResolvedValue(listing());
  vi.mocked(getCodeFile)
    .mockReset()
    .mockImplementation(async (_slug, path) => file(path));
  vi.mocked(searchCode).mockReset();
});

const tree = () => within(screen.getByRole("list", { name: "Strom súborov" }));

describe("NS Code (DEV-86)", () => {
  it("without a project asks for one and reads nothing", () => {
    context.selectedProject = null;
    page();

    expect(screen.getByText("Nemáš vybraný projekt")).toBeInTheDocument();
    expect(getCodeFiles).not.toHaveBeenCalled();
  });

  it("shows the tree folded, opens a folder, and a file from it in a tab with its path", async () => {
    page();

    await screen.findByRole("list", { name: "Strom súborov" });
    expect(getCodeFiles).toHaveBeenCalledWith("nex-inbox");
    expect(tree().queryByRole("button", { name: "main.py" })).not.toBeInTheDocument();
    fireEvent.click(tree().getByRole("button", { name: "backend" }));
    fireEvent.click(tree().getByRole("button", { name: "main.py" }));

    expect(await screen.findByTestId("editor")).toHaveAttribute("data-path", "backend/main.py");
    expect(screen.getByTestId("editor")).toHaveTextContent("# backend/main.py");
    expect(getCodeFile).toHaveBeenCalledWith("nex-inbox", "backend/main.py");
    expect(screen.getByRole("tab", { name: "main.py" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("backend › main.py")).toHaveAttribute("title", "backend/main.py");
    expect(screen.getByTestId("where")).toHaveTextContent("/ns-code?path=backend%2Fmain.py");
  });

  it("a link with a line opens the file at that line, with its folders open", async () => {
    page("/ns-code?path=backend/apps/ubl.py&line=129");

    const editor = await screen.findByTestId("editor");
    expect(editor).toHaveAttribute("data-path", "backend/apps/ubl.py");
    expect(editor).toHaveAttribute("data-line", "129");
    await screen.findByRole("list", { name: "Strom súborov" });
    expect(tree().getByRole("button", { name: "apps" })).toHaveAttribute("aria-expanded", "true");
    expect(tree().getByRole("button", { name: "ubl.py" })).toHaveAttribute("aria-current", "true");
  });

  it("a file is fetched once, however often its tab is chosen", async () => {
    page("/ns-code?path=README.md");
    await screen.findByTestId("editor");
    fireEvent.click(tree().getByRole("button", { name: "backend" }));
    fireEvent.click(tree().getByRole("button", { name: "main.py" }));
    await waitFor(() => expect(screen.getByTestId("editor")).toHaveAttribute("data-path", "backend/main.py"));

    fireEvent.click(screen.getByRole("tab", { name: "README.md" }));
    await waitFor(() => expect(screen.getByTestId("editor")).toHaveAttribute("data-path", "README.md"));

    expect(vi.mocked(getCodeFile).mock.calls.filter(([, p]) => p === "README.md")).toHaveLength(1);
  });

  it("closing the shown tab shows the one before it", async () => {
    page("/ns-code?path=README.md");
    await screen.findByTestId("editor");
    fireEvent.click(tree().getByRole("button", { name: "backend" }));
    fireEvent.click(tree().getByRole("button", { name: "main.py" }));
    await waitFor(() => expect(screen.getByTestId("editor")).toHaveAttribute("data-path", "backend/main.py"));

    fireEvent.click(screen.getByRole("button", { name: "Zavrieť main.py" }));

    await waitFor(() => expect(screen.getByTestId("editor")).toHaveAttribute("data-path", "README.md"));
    expect(screen.queryByRole("tab", { name: "main.py" })).not.toBeInTheDocument();
  });

  it("Ctrl+P opens a file by its name — Enter takes the best match", async () => {
    page();
    await screen.findByRole("list", { name: "Strom súborov" });

    fireEvent.keyDown(window, { key: "p", ctrlKey: true });
    const dialog = await screen.findByRole("dialog", { name: "Otvoriť súbor podľa mena" });
    fireEvent.change(within(dialog).getByLabelText("Meno súboru"), { target: { value: "app" } });
    fireEvent.keyDown(within(dialog).getByLabelText("Meno súboru"), { key: "Enter" });

    expect(await screen.findByTestId("editor")).toHaveAttribute("data-path", "frontend/src/App.tsx");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("a text search lists the lines by file and a line opens the file there", async () => {
    vi.mocked(searchCode).mockResolvedValue({
      query: "SellersItem",
      hits: [
        { path: "backend/apps/ubl.py", line: 214, text: "    seller = SellersItemIdentification" },
        { path: "backend/apps/ubl.py", line: 230, text: "    # SellersItem again" },
        { path: "README.md", line: 3, text: "SellersItem in the docs" },
      ],
      truncated: true,
    });
    page();
    await screen.findByRole("list", { name: "Strom súborov" });

    fireEvent.change(screen.getByLabelText("Hľadať text v kóde projektu"), { target: { value: "SellersItem" } });
    fireEvent.click(screen.getByRole("button", { name: "Hľadať" }));

    const results = await screen.findByRole("list", { name: "Výsledky hľadania" });
    expect(searchCode).toHaveBeenCalledWith("nex-inbox", "SellersItem");
    const said = (_: string, el: Element | null) => el?.tagName === "P" && el.textContent?.startsWith("„") === true;
    // The list stands there at once; the sentence and the lines come with the server's answer — waited for.
    expect((await screen.findByText(said)).textContent).toBe(
      "„SellersItem“: 3 riadkov v 2 súboroch — zobrazené sú prvé, spresni hľadanie.",
    );
    fireEvent.click(within(results).getByRole("button", { name: /230/ }));

    const editor = await screen.findByTestId("editor");
    expect([editor.getAttribute("data-path"), editor.getAttribute("data-line")]).toEqual([
      "backend/apps/ubl.py",
      "230",
    ]);
  });

  it("a short search says what the server said", async () => {
    vi.mocked(searchCode).mockRejectedValue(
      new ApiError(422, "Napíš aspoň 2 znaky.", { detail: "Napíš aspoň 2 znaky." }),
    );
    page();
    await screen.findByRole("list", { name: "Strom súborov" });

    fireEvent.change(screen.getByLabelText("Hľadať text v kóde projektu"), { target: { value: "a" } });
    fireEvent.click(screen.getByRole("button", { name: "Hľadať" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Napíš aspoň 2 znaky.");
  });

  it.each([
    [{ binary: true, text: null, size: 2048 }, "Binárny súbor (2 KB) — NS Code ukazuje len text."],
    [{ too_big: true, text: null, size: 3_500_000 }, "Súbor má 3,3 MB; NS Code ukazuje súbory do 977 KB."],
  ])("a file the editor does not show says why (%o)", async (over, sentence) => {
    vi.mocked(getCodeFile).mockResolvedValue(file("logo.png", over));
    page("/ns-code?path=logo.png");

    expect(await screen.findByText(sentence)).toBeInTheDocument();
    expect(screen.queryByTestId("editor")).not.toBeInTheDocument();
  });

  it("a project without git says the server's sentence", async () => {
    vi.mocked(getCodeFiles).mockRejectedValue(
      new ApiError(409, "conflict", { detail: "Projekt nemá na serveri git repozitár — NS Code nemá čo ukázať." }),
    );
    page();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Projekt nemá na serveri git repozitár — NS Code nemá čo ukázať.",
    );
  });
});

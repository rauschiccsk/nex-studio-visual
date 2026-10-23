/**
 * DEV-87 — NS Code shows what the agent changes: M and U marks in the tree, at the folders on the way and on the tabs;
 * an open file refreshed when it changes (and only then); when a file last changed and in which task; the file side by
 * side with the last commit or with where the selected version began.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import NsCodePage from "@/pages/NsCodePage";
import {
  getCodeFile,
  getCodeFiles,
  getCodeOriginal,
  type CodeFile,
  type CodeFiles,
  type CodeOriginal,
} from "@/services/api/nsCode";

vi.mock("@/services/api/nsCode", () => ({
  getCodeFiles: vi.fn(),
  getCodeFile: vi.fn(),
  getCodeOriginal: vi.fn(),
  searchCode: vi.fn(),
}));
vi.mock("@/components/nscode/CodeEditor", () => ({
  default: ({ path, text }: { path: string; text: string }) => (
    <pre data-testid="editor" data-path={path}>
      {text}
    </pre>
  ),
}));
vi.mock("@/components/nscode/CodeDiff", () => ({
  default: ({ original, modified }: { original: string; modified: string }) => (
    <div data-testid="diff">
      <pre data-testid="before">{original}</pre>
      <pre data-testid="after">{modified}</pre>
    </div>
  ),
}));
vi.mock("@/hooks/useTheme", () => ({ useTheme: () => ({ isDark: false, toggleDark: vi.fn() }) }));

const context = {
  selectedProject: { slug: "nex-inbox", name: "NEX Inbox" },
  selectedVersion: { versionId: "v180", versionNumber: "1.8.0" } as { versionId: string; versionNumber: string } | null,
  setSelectedVersion: vi.fn(),
};
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof context) => unknown) => selector(context),
}));

const FILES = ["README.md", "backend/main.py", "backend/new.py"];

function listing(over: Partial<CodeFiles> = {}): CodeFiles {
  return {
    head: "d626301458",
    files: FILES,
    changes: { "backend/main.py": "M", "backend/new.py": "U" },
    stamps: { "README.md": "10-1", "backend/main.py": "20-1", "backend/new.py": "30-1" },
    refresh_seconds: 10,
    ...over,
  };
}

function file(path: string, over: Partial<CodeFile> = {}): CodeFile {
  return {
    path,
    size: 20,
    text: `${path} v1\n`,
    binary: false,
    too_big: false,
    max_bytes: 1_000_000,
    stamp: listing().stamps[path] ?? "",
    last_commit: null,
    ...over,
  };
}

function original(over: Partial<CodeOriginal> = {}): CodeOriginal {
  return {
    path: "backend/main.py",
    against: "head",
    rev: "abc",
    existed: true,
    text: "main before\n",
    binary: false,
    too_big: false,
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
        <Route path="/ns-code" element={<NsCodePage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  context.selectedVersion = { versionId: "v180", versionNumber: "1.8.0" };
  context.setSelectedVersion.mockReset();
  vi.mocked(getCodeFiles).mockReset().mockResolvedValue(listing());
  vi.mocked(getCodeFile)
    .mockReset()
    .mockImplementation(async (_s, p) => file(p));
  vi.mocked(getCodeOriginal).mockReset().mockResolvedValue(original());
});

afterEach(() => {
  vi.useRealTimers();
});

const tree = () => within(screen.getByRole("list", { name: "Strom súborov" }));

describe("NS Code shows what the agent changes (DEV-87)", () => {
  it("marks a changed and a new file, the folder on the way and the open tab", async () => {
    page("/ns-code?path=backend/main.py");
    await screen.findByTestId("editor");

    const folder = tree().getByRole("button", { name: /backend/ });
    expect(within(folder).getByLabelText("Obsahuje zmeny")).toBeInTheDocument();
    expect(within(tree().getByRole("button", { name: /main\.py/ })).getByText("M")).toBeInTheDocument();
    expect(within(tree().getByRole("button", { name: /new\.py/ })).getByText("U")).toBeInTheDocument();
    expect(within(tree().getByRole("button", { name: /README/ })).queryByText(/^[MU]$/)).not.toBeInTheDocument();
    expect(
      screen.getByLabelText("Zmenený voči poslednému commitu", { selector: "[role=tablist] *" }),
    ).toHaveTextContent("M");
  });

  it("refreshes on the server's schedule and fetches again only the open file that changed", async () => {
    vi.useFakeTimers();
    vi.mocked(getCodeFiles).mockResolvedValue(listing({ refresh_seconds: 7 }));
    page("/ns-code?path=backend/main.py");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId("editor")).toHaveTextContent("backend/main.py v1");
    vi.mocked(getCodeFiles).mockResolvedValue(
      listing({
        refresh_seconds: 7,
        stamps: { "README.md": "10-1", "backend/main.py": "21-2", "backend/new.py": "30-1" },
      }),
    );
    vi.mocked(getCodeFile).mockImplementation(async (_s, p) => file(p, { text: `${p} v2\n`, stamp: "21-2" }));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_000);
    });
    expect(vi.mocked(getCodeFiles)).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(vi.mocked(getCodeFiles)).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("editor")).toHaveTextContent("backend/main.py v2");
    expect(vi.mocked(getCodeFile).mock.calls.map(([, p]) => p)).toEqual(["backend/main.py", "backend/main.py"]);
  });

  it("says when the file last changed and its task opens the task plan", async () => {
    vi.mocked(getCodeFile).mockResolvedValue(
      file("backend/main.py", {
        last_commit: {
          sha: "1771419bb2f6",
          at: "2026-10-11T14:30:00Z",
          subject: "test: gate (TASK #32.2.1)",
          task: { label: "32.2.1", task_id: "t", title: "Brána", version_id: "v180", version_number: "1.8.0" },
        },
      }),
    );
    page("/ns-code?path=backend/main.py");

    expect(await screen.findByTitle("test: gate (TASK #32.2.1)")).toHaveTextContent(/^Posledná zmena .+ · 1771419$/);
    fireEvent.click(screen.getByRole("button", { name: "Úloha 32.2.1 · verzia 1.8.0" }));

    expect(context.setSelectedVersion).toHaveBeenCalledWith({ versionId: "v180", versionNumber: "1.8.0" });
    expect(screen.getByTestId("where")).toHaveTextContent("/riadiace-centrum?uloha=32.2.1");
  });

  it("compares with the last commit side by side, and back to the file", async () => {
    page("/ns-code?path=backend/main.py");
    await screen.findByTestId("editor");

    fireEvent.click(screen.getByRole("button", { name: "S posledným commitom" }));

    expect(await screen.findByTestId("before")).toHaveTextContent("main before");
    expect(screen.getByTestId("after")).toHaveTextContent("backend/main.py v1");
    expect(getCodeOriginal).toHaveBeenCalledWith("nex-inbox", "backend/main.py", "head", "v180");
    fireEvent.click(screen.getByRole("button", { name: "S posledným commitom" }));
    expect(await screen.findByTestId("editor")).toBeInTheDocument();
  });

  it("compares with where the selected version began", async () => {
    vi.mocked(getCodeOriginal).mockResolvedValue(original({ against: "base", text: "main at 1.7.1\n" }));
    page("/ns-code?path=backend/main.py");
    await screen.findByTestId("editor");

    fireEvent.click(screen.getByRole("button", { name: "So začiatkom verzie 1.8.0" }));

    expect(await screen.findByTestId("before")).toHaveTextContent("main at 1.7.1");
    expect(getCodeOriginal).toHaveBeenCalledWith("nex-inbox", "backend/main.py", "base", "v180");
  });

  it("without a selected version only the last commit can be compared with", async () => {
    context.selectedVersion = null;
    page("/ns-code?path=backend/main.py");
    await screen.findByTestId("editor");

    expect(screen.getByRole("button", { name: "S posledným commitom" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /So začiatkom verzie/ })).not.toBeInTheDocument();
  });

  it("a new file says it did not exist then", async () => {
    vi.mocked(getCodeOriginal).mockResolvedValue(original({ path: "backend/new.py", existed: false, text: null }));
    page("/ns-code?path=backend/new.py");
    await screen.findByTestId("editor");

    fireEvent.click(screen.getByRole("button", { name: "S posledným commitom" }));

    expect(await screen.findByText("Súbor je nový — v poslednom commite ešte neexistoval.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("diff")).not.toBeInTheDocument());
  });
});

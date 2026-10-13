import { lazy, Suspense, useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  ChevronDown,
  ChevronRight,
  File,
  FileCode,
  FileCog,
  FileImage,
  FileJson,
  FileTerminal,
  FileText,
  Files,
  Folder,
  FolderOpen,
  Loader2,
  Search,
  X,
} from "lucide-react";
import { useWideScreen } from "nex-shared";

import { useTheme } from "@/contexts/ThemeContext";
import { buildTree, fileKind, foldersOf, quickOpen, type FileKind, type TreeNode } from "@/lib/nsCode";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import {
  getCodeFile,
  getCodeFiles,
  searchCode,
  type CodeFile,
  type CodeFiles,
  type CodeSearch,
} from "@/services/api/nsCode";
import { useActiveContextStore } from "@/store/activeContextStore";

/**
 * DEV-86 — NS Code (NEX Studio Code): the selected project's files as VS Code shows them, read-only. The server decides
 * what may be shown — what git knows or would add, never a secret — once for the tree, the file and the search. The
 * address carries the file and the line (`/ns-code?path=backend/main.py&line=129`), so a link opens the exact place.
 */

// The editor of VS Code is several megabytes: its own chunk, loaded when this tab opens, from the cockpit itself.
const CodeEditor = lazy(() => import("@/components/nscode/CodeEditor"));

const KIND_ICON: Record<FileKind, { icon: typeof File; tone: string }> = {
  code: { icon: FileCode, tone: "text-sky-500" },
  data: { icon: FileJson, tone: "text-amber-500" },
  text: { icon: FileText, tone: "text-[var(--color-text-secondary)]" },
  image: { icon: FileImage, tone: "text-fuchsia-500" },
  config: { icon: FileCog, tone: "text-[var(--color-text-muted)]" },
  shell: { icon: FileTerminal, tone: "text-emerald-500" },
  other: { icon: File, tone: "text-[var(--color-text-muted)]" },
};

const ROW =
  "flex w-full min-w-0 items-center gap-1.5 rounded px-1.5 py-0.5 text-left text-xs hover:bg-[var(--color-surface-hover)] pointer-coarse:min-h-11";

type Loaded = { file: CodeFile } | { error: HumanError };

function sizeWords(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} MB`;
}

const nameOf = (path: string) => path.slice(path.lastIndexOf("/") + 1);

export default function NsCodePage() {
  const navigate = useNavigate();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const slug = selectedProject?.slug;
  const { isDark } = useTheme();
  const wide = useWideScreen();
  const [params, setParams] = useSearchParams();
  const path = params.get("path");
  const line = Number(params.get("line")) || null;

  const [files, setFiles] = useState<CodeFiles | null>(null);
  const [listError, setListError] = useState<HumanError | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [tabs, setTabs] = useState<string[]>([]);
  const [loaded, setLoaded] = useState<Record<string, Loaded>>({});
  const [pane, setPane] = useState<"files" | "code">("files");
  const [view, setView] = useState<"tree" | "search">("tree");
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<CodeSearch | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<HumanError | null>(null);
  const [opening, setOpening] = useState(false);

  useEffect(() => {
    setFiles(null);
    setListError(null);
    setTabs([]);
    setLoaded({});
    setFound(null);
    if (!slug) return;
    let cancelled = false;
    Promise.resolve()
      .then(() => getCodeFiles(slug))
      .then((f) => !cancelled && setFiles(f))
      .catch((e: unknown) => {
        // The server's own sentence (no git checkout, …) comes through in the message.
        if (!cancelled) setListError(humanizeApiError(e, "Načítanie súborov zlyhalo"));
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const open = useCallback(
    (to: string, at?: number) => {
      const next: Record<string, string> = { path: to };
      if (at) next.line = String(at);
      setParams(next, { replace: true });
      setOpening(false);
    },
    [setParams],
  );

  // The file in the address is the one shown: it gets a tab, its folders open, its text is fetched once.
  useEffect(() => {
    if (!slug || !path) return;
    setTabs((t) => (t.includes(path) ? t : [...t, path]));
    setExpanded((e) => new Set([...e, ...foldersOf(path)]));
    setPane("code");
    if (loaded[path]) return;
    let cancelled = false;
    Promise.resolve()
      .then(() => getCodeFile(slug, path))
      .then((file) => !cancelled && setLoaded((l) => ({ ...l, [path]: { file } })))
      .catch((e: unknown) => {
        if (!cancelled)
          setLoaded((l) => ({ ...l, [path]: { error: humanizeApiError(e, "Súbor sa nepodarilo otvoriť") } }));
      });
    return () => {
      cancelled = true;
    };
    // `loaded` is read, not followed: a fetched file is never fetched again, and its arrival must not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, path]);

  // Ctrl+P (⌘P) opens the quick open, as in VS Code.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "p") {
        e.preventDefault();
        setOpening(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const tree = useMemo(() => buildTree(files?.files ?? []), [files]);

  function close(tab: string) {
    const left = tabs.filter((t) => t !== tab);
    setTabs(left);
    if (tab === path) {
      const next = left[left.length - 1];
      setParams(next ? { path: next } : {}, { replace: true });
    }
  }

  async function search(e: FormEvent) {
    e.preventDefault();
    if (!slug) return;
    setView("search");
    setPane("files");
    setSearching(true);
    setSearchError(null);
    try {
      setFound(await searchCode(slug, query));
    } catch (err: unknown) {
      setSearchError(humanizeApiError(err, "Hľadanie zlyhalo"));
      setFound(null);
    } finally {
      setSearching(false);
    }
  }

  if (!slug) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
        <FolderOpen className="h-10 w-10 text-[var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Nemáš vybraný projekt</h2>
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          NS Code je viazaný na projekt. Otvor <span className="font-mono">Projekty</span> a pripni projekt.
        </p>
        <button
          onClick={() => navigate("/projects")}
          className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 pointer-coarse:min-h-11"
        >
          Otvoriť Projekty
        </button>
      </div>
    );
  }

  const current = path ? loaded[path] : undefined;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-border-default)] px-3 py-2">
        <h1 className="text-sm font-bold text-[var(--color-text-primary)]">NS Code</h1>
        <span className="min-w-0 truncate text-xs text-[var(--color-text-muted)]">
          {selectedProject?.name}
          {files?.head ? ` · ${files.head.slice(0, 7)}` : ""}
          {files ? ` · ${files.files.length} súborov` : ""}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setPane(pane === "files" ? "code" : "files")}
            className="flex items-center gap-1 rounded-lg border border-[var(--color-border-default)] px-2 py-1 text-xs md:hidden pointer-coarse:min-h-11"
          >
            <Files className="h-3.5 w-3.5" /> {pane === "files" ? "Kód" : "Súbory"}
          </button>
          <button
            type="button"
            aria-haspopup="dialog"
            onClick={() => setOpening(true)}
            title="Otvoriť súbor podľa mena (Ctrl+P)"
            className="flex items-center gap-1 rounded-lg border border-[var(--color-border-default)] px-2 py-1 text-xs pointer-coarse:min-h-11"
          >
            <Search className="h-3.5 w-3.5" /> Otvoriť súbor
          </button>
          <form onSubmit={search} className="flex items-center gap-1" role="search">
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Hľadať v kóde"
              aria-label="Hľadať text v kóde projektu"
              spellCheck={false}
              className="w-40 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1 text-xs sm:w-56 pointer-coarse:min-h-11"
            />
            <button
              type="submit"
              className="rounded-lg bg-primary-600 px-2 py-1 text-xs font-medium text-white hover:bg-primary-500 pointer-coarse:min-h-11"
            >
              Hľadať
            </button>
          </form>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <aside
          aria-label="Súbory projektu"
          className={`min-h-0 w-full flex-shrink-0 overflow-y-auto border-r border-[var(--color-border-default)] p-2 md:block md:w-72 ${
            pane === "files" ? "" : "max-md:hidden"
          }`}
        >
          {view === "search" && (
            <SearchResults
              found={found}
              busy={searching}
              error={searchError}
              onOpen={open}
              onBack={() => setView("tree")}
            />
          )}
          {view === "tree" &&
            (listError ? (
              <p role="alert" className="p-2 text-xs text-[var(--color-state-error-fg)]">
                {listError.message}
              </p>
            ) : !files ? (
              <p className="flex items-center gap-1.5 p-2 text-xs text-[var(--color-text-muted)]">
                <Loader2 className="h-3 w-3 animate-spin" /> Načítavam súbory…
              </p>
            ) : (
              <ul aria-label="Strom súborov">
                {tree.map((node) => (
                  <TreeItem
                    key={node.path}
                    node={node}
                    depth={0}
                    expanded={expanded}
                    active={path}
                    onToggle={(p) =>
                      setExpanded((e) => {
                        const next = new Set(e);
                        if (next.has(p)) next.delete(p);
                        else next.add(p);
                        return next;
                      })
                    }
                    onOpen={(p) => open(p)}
                  />
                ))}
              </ul>
            ))}
        </aside>

        <section
          aria-label="Otvorený súbor"
          className={`flex min-h-0 min-w-0 flex-1 flex-col ${pane === "code" ? "" : "max-md:hidden"}`}
        >
          {tabs.length > 0 && (
            <div
              role="tablist"
              aria-label="Otvorené súbory"
              className="flex overflow-x-auto border-b border-[var(--color-border-default)]"
              data-scroll-x
            >
              {tabs.map((tab) => (
                <div
                  key={tab}
                  className={`flex flex-shrink-0 items-center border-r border-[var(--color-border-default)] ${
                    tab === path ? "bg-[var(--color-surface)]" : ""
                  }`}
                >
                  <button
                    type="button"
                    role="tab"
                    aria-selected={tab === path}
                    title={tab}
                    onClick={() => open(tab)}
                    className="px-2 py-1 text-xs text-[var(--color-text-secondary)] pointer-coarse:min-h-11"
                  >
                    {nameOf(tab)}
                  </button>
                  <button
                    type="button"
                    aria-label={`Zavrieť ${nameOf(tab)}`}
                    onClick={() => close(tab)}
                    className="flex items-center justify-center px-1 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] pointer-coarse:min-h-11 pointer-coarse:min-w-11"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
          {path && (
            <p className="truncate px-3 py-1 font-mono text-[11px] text-[var(--color-text-muted)]" title={path}>
              {path.split("/").join(" › ")}
            </p>
          )}
          <div className="min-h-0 flex-1">
            {!path ? (
              <p className="p-6 text-center text-xs text-[var(--color-text-muted)]">
                Vyber súbor v strome alebo ho otvor podľa mena (Ctrl+P).
              </p>
            ) : !current ? (
              <p className="flex items-center gap-1.5 p-4 text-xs text-[var(--color-text-muted)]">
                <Loader2 className="h-3 w-3 animate-spin" /> Otváram súbor…
              </p>
            ) : "error" in current ? (
              <p role="alert" className="p-4 text-xs text-[var(--color-state-error-fg)]">
                {current.error.message}
              </p>
            ) : current.file.binary ? (
              <p className="p-4 text-xs text-[var(--color-text-secondary)]">
                Binárny súbor ({sizeWords(current.file.size)}) — NS Code ukazuje len text.
              </p>
            ) : current.file.too_big ? (
              <p className="p-4 text-xs text-[var(--color-text-secondary)]">
                Súbor má {sizeWords(current.file.size)}; NS Code ukazuje súbory do {sizeWords(current.file.max_bytes)}.
              </p>
            ) : (
              <Suspense fallback={<p className="p-4 text-xs text-[var(--color-text-muted)]">Otváram editor…</p>}>
                <CodeEditor path={path} text={current.file.text ?? ""} line={line} dark={isDark} wide={wide} />
              </Suspense>
            )}
          </div>
        </section>
      </div>

      {opening && files && <QuickOpen files={files.files} onOpen={open} onClose={() => setOpening(false)} />}
    </div>
  );
}

function TreeItem(props: {
  node: TreeNode;
  depth: number;
  expanded: Set<string>;
  active: string | null;
  onToggle: (path: string) => void;
  onOpen: (path: string) => void;
}) {
  const { node, depth, expanded, active, onToggle, onOpen } = props;
  const indent = { paddingLeft: `${depth * 12 + 4}px` };
  if (node.folder) {
    const isOpen = expanded.has(node.path);
    const Icon = isOpen ? FolderOpen : Folder;
    return (
      <li>
        <button type="button" aria-expanded={isOpen} onClick={() => onToggle(node.path)} className={ROW} style={indent}>
          {isOpen ? (
            <ChevronDown className="h-3 w-3 flex-shrink-0" />
          ) : (
            <ChevronRight className="h-3 w-3 flex-shrink-0" />
          )}
          <Icon className="h-3.5 w-3.5 flex-shrink-0 text-amber-400" aria-hidden />
          <span className="truncate text-[var(--color-text-secondary)]">{node.name}</span>
        </button>
        {isOpen && (
          <ul>
            {node.children.map((child) => (
              <TreeItem key={child.path} {...props} node={child} depth={depth + 1} />
            ))}
          </ul>
        )}
      </li>
    );
  }
  const { icon: Icon, tone } = KIND_ICON[fileKind(node.name)];
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(node.path)}
        aria-current={node.path === active ? "true" : undefined}
        className={`${ROW} ${node.path === active ? "bg-primary-600/10 text-[var(--color-accent-primary)]" : ""}`}
        style={{ paddingLeft: `${depth * 12 + 19}px` }}
      >
        <Icon className={`h-3.5 w-3.5 flex-shrink-0 ${tone}`} aria-hidden />
        <span className="truncate">{node.name}</span>
      </button>
    </li>
  );
}

function SearchResults(props: {
  found: CodeSearch | null;
  busy: boolean;
  error: HumanError | null;
  onOpen: (path: string, line: number) => void;
  onBack: () => void;
}) {
  const { found, busy, error, onOpen, onBack } = props;
  const byFile = useMemo(() => {
    const groups = new Map<string, CodeSearch["hits"]>();
    for (const hit of found?.hits ?? []) groups.set(hit.path, [...(groups.get(hit.path) ?? []), hit]);
    return [...groups.entries()];
  }, [found]);
  return (
    <div>
      <button type="button" onClick={onBack} className={`${ROW} mb-1 text-[var(--color-accent-primary)]`}>
        <ChevronRight className="h-3 w-3 rotate-180" /> Späť na súbory
      </button>
      {busy && (
        <p className="flex items-center gap-1.5 p-2 text-xs text-[var(--color-text-muted)]">
          <Loader2 className="h-3 w-3 animate-spin" /> Hľadám…
        </p>
      )}
      {error && (
        <p role="alert" className="p-2 text-xs text-[var(--color-state-error-fg)]">
          {error.message}
        </p>
      )}
      {found && !busy && (
        <p className="px-1 pb-1 text-xs text-[var(--color-text-muted)]">
          „{found.query}“: {found.hits.length} riadkov v {byFile.length} súboroch
          {found.truncated ? " — zobrazené sú prvé, spresni hľadanie" : ""}.
        </p>
      )}
      <ul aria-label="Výsledky hľadania">
        {byFile.map(([file, hits]) => (
          <li key={file} className="mb-1">
            <p className="truncate px-1 font-mono text-[11px] text-[var(--color-text-secondary)]" title={file}>
              {file}
            </p>
            {hits.map((hit) => (
              <button
                key={hit.line}
                type="button"
                onClick={() => onOpen(hit.path, hit.line)}
                className={`${ROW} font-mono`}
              >
                <span className="w-8 flex-shrink-0 text-right text-[var(--color-text-muted)]">{hit.line}</span>
                <span className="truncate">{hit.text.trim()}</span>
              </button>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

function QuickOpen({
  files,
  onOpen,
  onClose,
}: {
  files: string[];
  onOpen: (path: string) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const matches = useMemo(() => quickOpen(files, text), [files, text]);
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Otvoriť súbor podľa mena"
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[10vh]"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] p-2 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2">
          <input
            autoFocus
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              if (e.key === "Enter" && matches[0]) onOpen(matches[0]);
            }}
            placeholder="Meno súboru, napríklad main.py"
            aria-label="Meno súboru"
            spellCheck={false}
            className="min-w-0 flex-1 rounded border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-2 py-1.5 text-sm pointer-coarse:min-h-11"
          />
          <button
            type="button"
            aria-label="Zavrieť"
            onClick={onClose}
            className="flex items-center justify-center rounded p-1 text-[var(--color-text-muted)] pointer-coarse:min-h-11 pointer-coarse:min-w-11"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <ul className="mt-2 max-h-[50vh] overflow-y-auto" aria-label="Nájdené súbory">
          {matches.map((p) => (
            <li key={p}>
              <button type="button" onClick={() => onOpen(p)} className={ROW} title={p}>
                <span className="flex-shrink-0 font-medium">{nameOf(p)}</span>
                <span className="truncate text-[var(--color-text-muted)]">{p}</span>
              </button>
            </li>
          ))}
          {matches.length === 0 && (
            <li className="p-2 text-xs text-[var(--color-text-muted)]">Žiadny súbor sa nenašiel.</li>
          )}
        </ul>
      </div>
    </div>
  );
}

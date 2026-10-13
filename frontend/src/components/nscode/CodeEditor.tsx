import { useCallback, useEffect, useMemo, useRef } from "react";
import Editor, { loader } from "@monaco-editor/react";

import { languageOf } from "@/lib/nsCode";

import { monaco } from "./monaco";

// The editor comes from the cockpit's own bundle, never from the CDN @monaco-editor/react would load it from.
loader.config({ monaco });

interface Props {
  path: string;
  text: string;
  /** The line a link points at — scrolled to the middle and marked; `null` = the top. */
  line: number | null;
  dark: boolean;
  /** A narrow screen has no room for the overview of the file on the right. */
  wide: boolean;
}

/**
 * DEV-86 — one file in the editor of VS Code, read-only: highlighting, line numbers, indentation guides, folding,
 * the file's overview on the right, Ctrl+F and Ctrl+G. Loaded only with the NS Code tab (a chunk of its own).
 */
export default function CodeEditor({ path, text, line, dark, wide }: Props) {
  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const marked = useRef<monaco.editor.IEditorDecorationsCollection | null>(null);
  const language = useMemo(() => languageOf(path, monaco.languages.getLanguages()), [path]);

  const reveal = useCallback(() => {
    const editor = editorRef.current;
    marked.current?.clear();
    if (!editor || !line) return;
    editor.revealLineInCenter(line);
    editor.setPosition({ lineNumber: line, column: 1 });
    marked.current = editor.createDecorationsCollection([
      { range: new monaco.Range(line, 1, line, 1), options: { isWholeLine: true, className: "ns-code-line" } },
    ]);
  }, [line]);

  useEffect(reveal, [reveal, path, text]);

  return (
    <Editor
      path={path}
      value={text}
      language={language}
      theme={dark ? "vs-dark" : "vs"}
      onMount={(editor) => {
        editorRef.current = editor;
        reveal();
      }}
      loading={<p className="p-4 text-xs text-[var(--color-text-muted)]">Otváram editor…</p>}
      options={{
        readOnly: true,
        domReadOnly: true,
        automaticLayout: true,
        minimap: { enabled: wide },
        folding: true,
        guides: { indentation: true, bracketPairs: false },
        lineNumbers: "on",
        stickyScroll: { enabled: true },
        scrollBeyondLastLine: false,
        renderLineHighlight: "line",
        fontFamily: '"JetBrains Mono", ui-monospace, monospace',
        fontSize: 13,
        wordWrap: "off",
      }}
    />
  );
}

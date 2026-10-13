import { useMemo } from "react";
import { DiffEditor, loader } from "@monaco-editor/react";

import { languageOf } from "@/lib/nsCode";

import { monaco } from "./monaco";

loader.config({ monaco });

interface Props {
  path: string;
  /** The file as it was. */
  original: string;
  /** The file as it is now. */
  modified: string;
  dark: boolean;
  /** Side by side on a computer; one column on a narrow screen. */
  wide: boolean;
}

/** DEV-87 — a file compared with what it was, as VS Code shows changes: read-only, side by side. */
export default function CodeDiff({ path, original, modified, dark, wide }: Props) {
  const language = useMemo(() => languageOf(path, monaco.languages.getLanguages()), [path]);
  return (
    <DiffEditor
      original={original}
      modified={modified}
      language={language}
      theme={dark ? "vs-dark" : "vs"}
      loading={<p className="p-4 text-xs text-[var(--color-text-muted)]">Otváram porovnanie…</p>}
      options={{
        readOnly: true,
        originalEditable: false,
        renderSideBySide: wide,
        // On a computer side by side even in a narrower pane, as VS Code does; the editor would otherwise fold to one
        // column below 900 px of its own.
        useInlineViewWhenSpaceIsLimited: !wide,
        automaticLayout: true,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        fontFamily: '"JetBrains Mono", ui-monospace, monospace',
        fontSize: 13,
      }}
    />
  );
}

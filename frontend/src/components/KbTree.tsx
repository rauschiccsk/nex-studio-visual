/**
 * KbTree — unified hierarchical file-browser for Knowledge Base documents.
 *
 * Replaces the previous two-column layout (flat categories + flat documents)
 * with a single recursive tree:
 *
 *   📁 icc
 *      📄 CLAUDE_AUDITOR.md
 *      📄 CLAUDE_COMMON.md
 *   📁 projects
 *      📂 nex-inbox        ← expanded
 *         📄 STATUS.md
 *      📁 nex-studio       ← collapsed
 *
 * State:
 *   - ``expandedFolders``: which folders are open (in-memory only,
 *     resets on reload per AC1)
 *   - ``selectedPath``: bubbled up via props so the page-level component
 *     can drive viewer load
 *
 * Auto-expansion: when ``selectedPath`` is set, all parent folders are
 * forced open so the user always sees the selected file (AC11).
 *
 * Tree data is computed via :file:`src/lib/kbTreeBuilder.ts` —
 * see :file:`src/__tests__/lib/test_kbTreeBuilder.test.ts` for the
 * sort + filter rules.
 */

import { useMemo, useState, useEffect } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { buildTree } from "@/lib/kbTreeBuilder";
import type { KnowledgeDoc, TreeNode } from "@/types/knowledge";

export interface KbTreeProps {
  documents: KnowledgeDoc[];
  selectedPath: string | null;
  onSelect: (doc: KnowledgeDoc) => void;
  hideCredentials?: boolean;
}

/** Indentation per depth level, in pixels. */
const INDENT_PX = 16;

/**
 * Collect every parent folder path for a given file path.
 * ``projects/nex-inbox/STATUS.md`` →  ``["projects", "projects/nex-inbox"]``.
 */
function parentFolderPaths(filePath: string): string[] {
  const parts = filePath.split("/");
  const out: string[] = [];
  let acc = "";
  for (let i = 0; i < parts.length - 1; i++) {
    const segment = parts[i] as string;
    acc = acc ? `${acc}/${segment}` : segment;
    out.push(acc);
  }
  return out;
}

export function KbTree({
  documents,
  selectedPath,
  onSelect,
  hideCredentials = false,
}: KbTreeProps) {
  const tree = useMemo(
    () => buildTree(documents, { hideCredentials }),
    [documents, hideCredentials],
  );

  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // AC11: keep parent folders of selectedPath in the expanded set.
  // We merge instead of replace so a user-expanded folder stays open
  // when selectedPath changes.
  useEffect(() => {
    if (!selectedPath) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      for (const p of parentFolderPaths(selectedPath)) next.add(p);
      return next;
    });
  }, [selectedPath]);

  const toggle = (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  return (
    <div className="flex flex-col gap-0.5">
      {tree.map((node) => (
        <TreeNodeView
          key={node.path}
          node={node}
          expanded={expanded}
          selectedPath={selectedPath}
          onToggle={toggle}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

interface TreeNodeViewProps {
  node: TreeNode;
  expanded: Set<string>;
  selectedPath: string | null;
  onToggle: (path: string) => void;
  onSelect: (doc: KnowledgeDoc) => void;
}

function TreeNodeView({
  node,
  expanded,
  selectedPath,
  onToggle,
  onSelect,
}: TreeNodeViewProps) {
  // padding-left starts at one INDENT_PX (depth 0 → 16px) so the
  // chevron has breathing room even at the top level.
  const indent = (node.depth + 1) * INDENT_PX;

  if (node.type === "folder") {
    const isOpen = expanded.has(node.path);
    return (
      <>
        <button
          type="button"
          onClick={() => onToggle(node.path)}
          style={{ paddingLeft: `${indent}px` }}
          className="flex w-full items-center gap-1.5 py-1 pr-2 text-left text-sm text-gray-200 hover:bg-gray-700"
        >
          {isOpen ? (
            <ChevronDown size={14} className="shrink-0 text-gray-400" />
          ) : (
            <ChevronRight size={14} className="shrink-0 text-gray-400" />
          )}
          <span
            className="shrink-0 text-base leading-none"
            aria-hidden="true"
          >
            {isOpen ? "📂" : "📁"}
          </span>
          <span className="truncate">{node.name}</span>
        </button>
        {isOpen &&
          node.children.map((child) => (
            <TreeNodeView
              key={child.path}
              node={child}
              expanded={expanded}
              selectedPath={selectedPath}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))}
      </>
    );
  }

  // node.type === "file"
  const isSelected = selectedPath === node.path;
  return (
    <button
      type="button"
      onClick={() => onSelect(node.doc)}
      style={{ paddingLeft: `${indent}px` }}
      className={[
        "flex w-full items-center gap-1.5 py-1 pr-2 text-left text-sm",
        isSelected
          ? "bg-blue-700 text-white"
          : "text-gray-300 hover:bg-gray-700",
      ].join(" ")}
    >
      {/* chevron-spacer so files align with folder names */}
      <span className="inline-block w-3.5 shrink-0" aria-hidden="true" />
      <span
        className="shrink-0 text-base leading-none"
        aria-hidden="true"
      >
        📄
      </span>
      <span className="truncate">{node.name}</span>
    </button>
  );
}

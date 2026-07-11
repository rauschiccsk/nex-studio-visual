import { Copy, Check } from "lucide-react";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";

interface CodeBlockProps {
  children: string;
  language?: string;
}

/**
 * Code block with copy-to-clipboard button.
 * Ported 1:1 from NEX Command `frontend/src/components/chat/CodeBlock.tsx`
 * per Director mandate 2026-05-07 (M1.D milestone).
 */
export function CodeBlock({ children, language }: CodeBlockProps) {
  const [copy, isCopied] = useCopyToClipboard();
  const code = String(children).replace(/\n$/, "");

  return (
    <div className="relative my-3">
      <div className="flex items-center justify-between bg-[var(--color-surface)] px-3 py-1.5 rounded-t border border-[var(--color-border-default)] border-b-0">
        <span className="text-xs text-[var(--color-text-secondary)]">{language || "kód"}</span>
        <button
          onClick={() => copy(code)}
          className="flex items-center gap-1 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors"
        >
          {isCopied ? (
            <>
              <Check className="w-3.5 h-3.5 text-[var(--color-status-success)]" />
              <span className="text-[var(--color-status-success)]">Skopírované</span>
            </>
          ) : (
            <>
              <Copy className="w-3.5 h-3.5" />
              <span>Kopírovať</span>
            </>
          )}
        </button>
      </div>
      <pre className="bg-[var(--color-canvas)] p-3 rounded-b border border-[var(--color-border-default)] border-t-0 overflow-x-auto m-0">
        <code className={`text-sm ${language ? `language-${language}` : ""}`}>{code}</code>
      </pre>
    </div>
  );
}

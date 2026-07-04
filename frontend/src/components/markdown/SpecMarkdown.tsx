// SpecMarkdown — the ONE shared Markdown renderer for spec / transcript bodies (spine STEP 2).
//
// Extracted from the byte-identical copy-paste that lived in BOTH ConversationThread and PhaseArtifact
// (fenced code → the shared CodeBlock; everything else default GFM). Kept deliberately NARROW: it renders
// ONLY the duplicated ReactMarkdown+remarkGfm+MARKDOWN_COMPONENTS core and does NOT bundle any prose
// wrapper/padding — the consumers' wrappers are NOT identical (chat bubbles carry no padding; the phase
// artifact adds ``px-4 py-3``), so each consumer keeps its own wrapper and passes it via the optional
// ``className``. Consumers: SpecifikaciaPage, ConversationThread (body + question), PhaseArtifact.

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { CodeBlock } from "./CodeBlock";

// Fenced code → the shared CodeBlock (language label + copy); everything else default GFM.
const MARKDOWN_COMPONENTS: Components = {
  code({ className, children, ...props }) {
    const match = /language-(\w+)/.exec(className || "");
    const inline = !className;
    if (!inline && match) return <CodeBlock language={match[1]}>{String(children)}</CodeBlock>;
    if (!inline) return <CodeBlock>{String(children)}</CodeBlock>;
    return (
      <code className="rounded bg-[var(--color-surface-hover)] px-1 py-0.5 text-[0.85em]" {...props}>
        {children}
      </code>
    );
  },
};

interface Props {
  /** The Markdown source to render (spec body, transcript bubble, or phase artifact). */
  body: string;
  /** Optional wrapper class — the consumer's own prose/padding. When omitted the renderer is bare (the
   *  consumer wraps it itself). Scoped this way so bubbles stay padding-free while the phase artifact keeps
   *  its ``px-4 py-3`` (the wrappers are NOT identical). */
  className?: string;
}

/** The single source of truth for rendering spec / transcript Markdown. */
export function SpecMarkdown({ body, className }: Props) {
  const rendered = (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
      {body}
    </ReactMarkdown>
  );
  return className ? <div className={className}>{rendered}</div> : rendered;
}

export default SpecMarkdown;

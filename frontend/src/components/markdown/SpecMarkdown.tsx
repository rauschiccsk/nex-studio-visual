// SpecMarkdown — the ONE shared Markdown renderer for spec / transcript bodies (spine STEP 2).
//
// Extracted from the byte-identical copy-paste that lived in BOTH ConversationThread and PhaseArtifact
// (fenced code → the shared CodeBlock; everything else default GFM). Kept deliberately NARROW: it renders
// ONLY the duplicated ReactMarkdown+remarkGfm+MARKDOWN_COMPONENTS core and does NOT bundle any prose
// wrapper/padding — the consumers' wrappers are NOT identical (chat bubbles carry no padding; the phase
// artifact adds ``px-4 py-3``), so each consumer keeps its own wrapper and passes it via the optional
// ``className``. Consumers: SpecifikaciaPage, ConversationThread (body + question), PhaseArtifact.

import { createElement, useMemo, type ComponentProps } from "react";
import ReactMarkdown, { type Components, type ExtraProps } from "react-markdown";
import { Link } from "react-router-dom";
import remarkGfm from "remark-gfm";

import { CODE_LINK_ATTRIBUTE, rehypeCodeLinks, textOf } from "@/lib/codeLinks";

import { CodeBlock } from "./CodeBlock";

// Fenced code → the shared CodeBlock (language label + copy); everything else default GFM.
const MARKDOWN_COMPONENTS: Components = {
  code({ className, children, node, ...props }) {
    const match = /language-(\w+)/.exec(className || "");
    const inline = !className;
    // DEV-88: a block may carry links to the project's files — it shows them, the copy button copies the plain text.
    if (!inline && match)
      return (
        <CodeBlock language={match[1]} copyText={textOf(node)}>
          {children}
        </CodeBlock>
      );
    if (!inline) return <CodeBlock copyText={textOf(node)}>{children}</CodeBlock>;
    return (
      <code className="rounded bg-[var(--color-surface-hover)] px-1 py-0.5 text-[0.85em]" {...props}>
        {children}
      </code>
    );
  },
};

/** DEV-88 — a link into NS Code stays inside the app; any other link is the consumer's or a plain one. */
function withCodeLinks(own?: Components["a"]): NonNullable<Components["a"]> {
  return function Anchor(props: ComponentProps<"a"> & ExtraProps & { [CODE_LINK_ATTRIBUTE]?: string }) {
    const { node, children, href, ...rest } = props;
    if (props[CODE_LINK_ATTRIBUTE] !== undefined && href) {
      return (
        <Link to={href} className="text-[var(--color-text-link)] underline">
          {children}
        </Link>
      );
    }
    if (own) return createElement(own, props);
    void node;
    return (
      <a href={href} {...rest}>
        {children}
      </a>
    );
  };
}

interface Props {
  /** The Markdown source to render (spec body, transcript bubble, or phase artifact). */
  body: string;
  /** Optional wrapper class — the consumer's own prose/padding. When omitted the renderer is bare (the
   *  consumer wraps it itself). Scoped this way so bubbles stay padding-free while the phase artifact keeps
   *  its ``px-4 py-3`` (the wrappers are NOT identical). */
  className?: string;
  /** Extra element renderers over the shared ones (DEV-83: Poradca draws its marked choice as a badge). */
  components?: Components;
  /**
   * DEV-88 — the files NS Code shows for the project: a path among them named in the text becomes a link to it. Without
   * it the text stays as it is.
   */
  codePaths?: ReadonlySet<string> | null;
  /** The project's slug — a path written with the project's place on the server (`/opt/projects/<slug>/…`) counts
   *  too. */
  projectSlug?: string;
}

/** The single source of truth for rendering spec / transcript Markdown. */
export function SpecMarkdown({ body, className, components, codePaths, projectSlug }: Props) {
  const rehypePlugins = useMemo(
    () => (codePaths ? [rehypeCodeLinks(codePaths, projectSlug)] : []),
    [codePaths, projectSlug],
  );
  const merged = useMemo(() => {
    const all = components ? { ...MARKDOWN_COMPONENTS, ...components } : MARKDOWN_COMPONENTS;
    return codePaths ? { ...all, a: withCodeLinks(components?.a) } : all;
  }, [components, codePaths]);
  const rendered = (
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={rehypePlugins} components={merged}>
      {body}
    </ReactMarkdown>
  );
  return className ? <div className={className}>{rendered}</div> : rendered;
}


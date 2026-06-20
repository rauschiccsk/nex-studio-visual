/**
 * Aktualizácie — per-version, user-facing changelog ("Čo je nové").
 *
 * Fetches ``GET /api/v1/release-notes`` (public, no auth) and renders each
 * shipped version as an expandable card, newest first (newest default-open).
 * The release date comes from the API response (``released_at`` — DB-sourced
 * with an mtime fallback), never parsed from the Markdown.
 *
 * Markdown is rendered with the inline ReactMarkdown idiom shared by
 * :file:`ProjectSpecsPage.tsx` / :file:`KnowledgeBasePage.tsx` (react-markdown
 * + remark-gfm + the local CodeBlock override) — no new dependency.
 */

import { useCallback, useEffect, useState } from "react";
import { Loader2, Sparkles, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Card } from "nex-shared";

import { CodeBlock } from "@/components/markdown/CodeBlock";
import { ApiError } from "@/services/api";
import { listReleaseNotes, type ReleaseNote } from "@/services/api/releaseNotes";

/** Format an ISO ``YYYY-MM-DD`` date as Slovak long form (e.g. ``20. jún 2026``).
 *  Parsed component-wise to avoid a UTC-midnight off-by-one in the local tz. */
function formatDate(iso: string | null): string {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return new Intl.DateTimeFormat("sk-SK", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(d);
}

/** Markdown body — the inline idiom shared with ProjectSpecsPage / KnowledgeBasePage. */
function MarkdownBody({ children }: { children: string }) {
  return (
    <div className="prose dark:prose-invert prose-sm max-w-none">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className, children, ...props }) {
            const match = /language-(\w+)/.exec(className || "");
            const isInline =
              !className && typeof children === "string" && !children.includes("\n");
            if (!isInline && match) {
              return <CodeBlock language={match[1]}>{String(children)}</CodeBlock>;
            }
            if (!isInline && typeof children === "string" && children.includes("\n")) {
              return <CodeBlock>{String(children)}</CodeBlock>;
            }
            return (
              <code
                className="bg-[var(--color-surface)] px-1.5 py-0.5 rounded text-sm"
                {...props}
              >
                {children}
              </code>
            );
          },
          pre({ children }) {
            return <>{children}</>;
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

interface VersionCardProps {
  note: ReleaseNote;
  defaultOpen: boolean;
}

function VersionCard({ note, defaultOpen }: VersionCardProps) {
  const date = formatDate(note.released_at);
  return (
    <Card className="p-0 overflow-hidden">
      <details open={defaultOpen} className="group">
        <summary className="flex items-center justify-between gap-3 px-4 py-3 cursor-pointer select-none list-none hover:bg-[var(--color-surface-hover)] transition-colors">
          <span className="flex items-center gap-2">
            <span className="text-sm font-semibold text-[var(--color-text-primary)]">
              {note.version}
            </span>
            {date && (
              <span className="text-xs text-[var(--color-text-muted)]">— {date}</span>
            )}
          </span>
          <svg
            className="w-4 h-4 shrink-0 text-[var(--color-text-muted)] transition-transform group-open:rotate-180"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </summary>
        <div className="px-4 pb-4 pt-1 border-t border-[var(--color-border-default)]">
          <MarkdownBody>{note.markdown}</MarkdownBody>
        </div>
      </details>
    </Card>
  );
}

export default function UpdatesPage() {
  const [notes, setNotes] = useState<ReleaseNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setNotes(await listReleaseNotes());
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : "Chyba pri načítaní aktualizácií",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <div className="mb-5">
        <h1 className="flex items-center gap-2 text-lg font-semibold text-[var(--color-text-primary)]">
          <Sparkles size={18} className="text-primary-500" />
          Aktualizácie
        </h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Čo je nové v jednotlivých verziách NEX Studio.
        </p>
      </div>

      {error && (
        <div className="mb-4 px-3 py-2 rounded-lg bg-[var(--color-state-error-bg)] text-[var(--color-state-error-fg)] text-sm flex items-center justify-between">
          <span className="truncate">{error}</span>
          <button onClick={() => setError("")} className="ml-2 hover:opacity-80">
            <X size={14} />
          </button>
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 py-10 justify-center text-[var(--color-text-secondary)] text-sm">
          <Loader2 size={16} className="animate-spin" /> Načítavam…
        </div>
      ) : notes.length === 0 ? (
        <div className="rounded-xl border border-dashed border-[var(--color-border-default)] p-10 text-center text-sm text-[var(--color-text-muted)]">
          Zatiaľ žiadne aktualizácie.
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {notes.map((note, i) => (
            <VersionCard key={note.version} note={note} defaultOpen={i === 0} />
          ))}
        </div>
      )}
    </div>
  );
}

// Live agent activity feed (F-007, CR-NS-018). Shown only while the agent is
// working — a streaming view of what the headless agent is doing (reads, writes,
// tool calls, partial reasoning), auto-scrolling. Ephemeral; not persisted.

import { useEffect, useRef } from "react";
import { FileText, Loader2, Terminal, Wrench } from "lucide-react";

import type { ActivityLine } from "../../services/api/pipeline";

function KindIcon({ kind, line }: { kind: ActivityLine["kind"]; line: string }) {
  if (kind === "text") return <FileText className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />;
  if (line.startsWith("spúšťa:")) return <Terminal className="h-3 w-3 shrink-0 text-[var(--color-status-info)]" />;
  return <Wrench className="h-3 w-3 shrink-0 text-[var(--color-status-success)]" />;
}

interface Props {
  activity: ActivityLine[];
}

export function PipelineActivityFeed({ activity }: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [activity.length]);

  return (
    // Director observation #2 — a DELIBERATE consistent fixed cap (max-h-64 = 256px), NOT content-adaptive: the
    // panel is always the same size and scrolls (overflow-y-auto) once full, so it never "randomly truncates" at
    // different content sizes. The cap stays so a long feed can't squeeze the message thread above it.
    <div className="flex max-h-64 flex-col overflow-y-auto border-b border-[var(--color-border-default)] bg-[var(--color-surface-hover)] px-4 py-2">
      {/* Blue = working (CR-NS-028): this feed only renders while the agent is working, so its accent
          follows the "in_progress/working = blue" taxonomy — not emerald (which means done). */}
      <div className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-status-info)]">
        <Loader2 className="h-3 w-3 animate-spin" />
        Živá aktivita agenta
      </div>
      {activity.length === 0 ? (
        <div className="text-[11px] text-[var(--color-text-muted)]">Agent štartuje…</div>
      ) : (
        <ul className="space-y-0.5">
          {activity.map((a, i) => (
            <li key={i} className="flex items-center gap-1.5 font-mono text-[11px] text-[var(--color-text-secondary)]">
              <KindIcon kind={a.kind} line={a.line} />
              <span className="truncate">{a.line}</span>
            </li>
          ))}
        </ul>
      )}
      <div ref={endRef} />
    </div>
  );
}

export default PipelineActivityFeed;

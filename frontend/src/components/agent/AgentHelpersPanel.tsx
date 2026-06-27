// AgentHelpersPanel — the "+ N pomocníci" panel on the AI Agent tab (CR-V2-022 / CR-V2-018, design §4.4.1).
//
// Appears when the AI Agent spawns ephemeral helper agents (CLI sub-agents / Task tool) for parallel/bulk
// sub-tasks, with a one-line description of what each is doing. HIDDEN when none are active (fed by the
// pipeline WS `helpers` frame — `count === 0` ⇒ this renders nothing). The Auditor is never a helper
// (independence, enforced backend-side) so it can never appear here.

import { Users } from "lucide-react";

import type { HelpersFeed } from "../../services/api/pipeline";

interface Props {
  helpers: HelpersFeed;
}

export function AgentHelpersPanel({ helpers }: Props) {
  // Hidden when none are active (design §4.4.1: "Hidden when none are active").
  if (helpers.count === 0) return null;

  return (
    <div className="flex-shrink-0 border-t border-[var(--color-border-default)] bg-[var(--color-surface-hover)] px-4 py-2">
      <div className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-status-info)]">
        <Users className="h-3 w-3" />
        {/* The Slovak "+ N pomocníci" header is grammar-corrected backend-side (CR-V2-018). */}
        {helpers.line}
      </div>
      <ul className="space-y-0.5">
        {helpers.helpers.map((desc, i) => (
          <li
            key={i}
            className="flex items-center gap-1.5 font-mono text-[11px] text-[var(--color-text-secondary)]"
          >
            <span className="text-[var(--color-status-info)]" aria-hidden="true">
              ↳
            </span>
            <span className="truncate">{desc}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default AgentHelpersPanel;

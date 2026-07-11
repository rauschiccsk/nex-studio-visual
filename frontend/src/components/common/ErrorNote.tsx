// ErrorNote — plain-Slovak error line + optional collapsible technical detail (audit Theme 2: error-framing).
//
// Renders a HumanError (produced by humanizeApiError): the manager-facing `.message` in plain Slovak, and —
// only when present — the raw technical `.detail` (HTTP status + backend text) tucked behind a "Technický
// detail" <details> disclosure. Every operate/cockpit catch block routes its error through humanizeApiError and
// renders it here, so a real failure never leaks a raw English backend detail to a non-expert Manažér.

import type { HumanError } from "@/services/apiError";

interface Props {
  error: HumanError | null;
  /** Optional wrapper classes (e.g. spacing) so call sites keep their existing layout. */
  className?: string;
}

export default function ErrorNote({ error, className }: Props) {
  if (!error) return null;
  return (
    <div className={className}>
      <p className="text-xs text-[var(--color-status-error)]">{error.message}</p>
      {error.detail && (
        <details className="mt-1">
          <summary className="cursor-pointer text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]">
            Technický detail
          </summary>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words rounded bg-[var(--color-surface-hover)] px-2 py-1 text-[10px] text-[var(--color-text-muted)]">
            {error.detail}
          </pre>
        </details>
      )}
    </div>
  );
}

// WarningActionBar — the shared amber "something is wrong, here is the one thing to do" bar (v4.0.57).
//
// Extracted from ReverifyBar (Riadiace centrum) and DeployBlockNotice (UAT/PROD), which had grown
// byte-identical chrome: the same warning strip, the same title+body+action row, the same ErrorNote tail and
// the same submit-in-flight button handling. Two surfaces of the SAME warning would have drifted apart on the
// next theme change — and they sit one click from each other in the drift flow a manager walks.
//
// The caller keeps what genuinely differs: its copy, its icons, and what the action does. Only the shape is
// shared. `variant` covers the two real placements rather than leaking Tailwind strings through props.
//
// NOT forced onto every amber surface: AuditorUpfrontReview shares these COLOUR TOKENS but not this
// structure (no action, no error, no header/body split), so it imports `WARNING_CHROME` alone. Sharing
// tokens is DRY; sharing a shape that does not fit would be abstraction for its own sake.

import type { ReactNode } from "react";
import { CircleAlert, type LucideIcon } from "lucide-react";

import ErrorNote from "./ErrorNote";
import type { HumanError } from "@/services/apiError";

/** The amber warning chrome — ONE definition for every surface that paints it. */
export const WARNING_CHROME =
  "border-l-4 border-l-[var(--color-state-warning-fg)] bg-[var(--color-state-warning-bg)]";

/** The single call-to-action a warning bar may carry. Omit it when the user has nothing to do here. */
export interface WarningBarAction {
  label: string;
  icon: LucideIcon;
  /** Spin the icon while the action is in flight. */
  spinning?: boolean;
  disabled?: boolean;
  /** Explains a disabled action — never leave a dead control unexplained. */
  title?: string;
  onClick: () => void;
}

export interface WarningActionBarProps {
  /** `docked` sits at the bottom of a column (square, top border); `card` is a standalone block. */
  variant: "docked" | "card";
  title: string;
  /** Defaults to the alert glyph; pass a spinner-capable icon for an in-progress state. */
  icon?: LucideIcon;
  iconSpinning?: boolean;
  /** The explanation — plain language, no jargon; this is what a non-expert reads. */
  children: ReactNode;
  action?: WarningBarAction | null;
  error?: HumanError | null;
}

const OUTER: Record<WarningActionBarProps["variant"], string> = {
  docked: "border-t border-[var(--color-border-default)] bg-[var(--color-surface)]",
  card: "mb-4 overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)]",
};

export default function WarningActionBar({
  variant,
  title,
  icon: Icon = CircleAlert,
  iconSpinning = false,
  children,
  action = null,
  error = null,
}: WarningActionBarProps) {
  const ActionIcon = action?.icon;
  return (
    <div className={OUTER[variant]}>
      <div
        className={`flex items-center gap-2 ${WARNING_CHROME} px-4 py-2.5 text-sm font-semibold text-[var(--color-state-warning-fg)]`}
      >
        <Icon className={`h-4 w-4 flex-shrink-0 ${iconSpinning ? "animate-spin" : ""}`} aria-hidden="true" />
        <span>{title}</span>
      </div>

      <div className="flex flex-col gap-2 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-[var(--color-text-muted)]">{children}</p>
          {action && ActionIcon && (
            <button
              type="button"
              onClick={action.onClick}
              disabled={action.disabled}
              title={action.title}
              className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ActionIcon
                className={`h-3.5 w-3.5 ${action.spinning ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
              {action.label}
            </button>
          )}
        </div>
        <ErrorNote error={error} />
      </div>
    </div>
  );
}

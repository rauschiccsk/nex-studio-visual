/**
 * DEV-192 — a page that crashes while it renders says so instead of leaving the window white.
 *
 * 23.10.2026 the cockpit had no error boundary: an exception while rendering a page unmounted the whole application
 * and nobody learned why — the Director's „prázdna stránka“ at Poradca had to be told apart from such a crash by a
 * screenshot. The boundary stands around the page's content, so the menu and the top bar stay; it says what happened,
 * offers „Obnoviť“ and writes the error to the server (`POST /client-errors`), where it can be found.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { AlertTriangle, RotateCw } from "lucide-react";

import { reportClientErrorApi } from "@/services/api/clientErrors";

interface Props {
  children: ReactNode;
}

interface InnerProps extends Props {
  /** The page with its query, as the browser has it — the report says where it happened. */
  path: string;
}

interface State {
  error: Error | null;
}

class Boundary extends Component<InnerProps, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // A report that does not arrive must not throw again — the screen already says what happened.
    void reportClientErrorApi({
      message: error.message || String(error),
      stack: `${error.stack ?? ""}\n${info.componentStack ?? ""}`.trim(),
      path: this.props.path,
      version: import.meta.env.VITE_APP_VERSION ?? "",
    }).catch(() => undefined);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div
        role="alert"
        className="m-4 rounded-xl border border-[var(--color-state-error-fg)]/40 bg-[var(--color-state-error-bg)] p-4"
      >
        <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--color-state-error-fg)]">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" /> Stránka
          narazila na chybu
        </h2>
        <p className="mt-2 text-sm text-[var(--color-text-primary)]">
          Túto stránku sa nepodarilo zobraziť. Kokpit chybu zapísal, takže sa dá
          dohľadať. Skús stránku obnoviť; ak sa to opakuje, povedz o tom Dedovi.
        </p>
        <p className="mt-1 font-mono text-[11px] text-[var(--color-text-muted)]">
          {error.message}
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-4 py-2 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)]"
        >
          <RotateCw className="h-3.5 w-3.5" aria-hidden="true" /> Obnoviť
        </button>
      </div>
    );
  }
}

/** The boundary for one page — a new page (another address) starts clean. */
export default function PageErrorBoundary({ children }: Props) {
  const location = useLocation();
  return (
    <Boundary
      key={location.pathname}
      path={`${location.pathname}${location.search}`}
    >
      {children}
    </Boundary>
  );
}

import { Button } from "nex-shared";

interface UpdateBannerProps {
  onApply: () => void;
  onDismiss: () => void;
}

/**
 * Offer of a newer version while the person is working.
 *
 * Deliberately `role="status"`, not `alert` - it is not an error, it is an offer. And
 * deliberately pinned to the BOTTOM: the session-expiry warning already sits at the top,
 * and two bars stacked there would clash. The app never reloads itself during work - the
 * decision belongs to the person, otherwise it could discard a half-filled form.
 */
export function UpdateBanner({ onApply, onDismiss }: UpdateBannerProps) {
  return (
    <div
      role="status"
      className="fixed inset-x-0 bottom-0 z-50 flex justify-center p-4 pointer-events-none"
    >
      <div className="pointer-events-auto flex w-full max-w-md items-center gap-3 rounded-lg border border-border-default bg-surface-elevated px-4 py-3 shadow-lg">
        <span className="flex-1 text-sm text-text-primary">
          Je k dispozícii novšia verzia.
        </span>
        <Button variant="primary" size="sm" onClick={onApply} type="button">
          Načítať
        </Button>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Zavrieť"
          className="shrink-0 rounded px-1 text-text-muted transition-colors hover:text-text-primary"
        >
          ✕
        </button>
      </div>
    </div>
  );
}

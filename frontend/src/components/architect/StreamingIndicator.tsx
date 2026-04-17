/**
 * Animated typing indicator shown while the Architect is streaming
 * a response.  Renders three pulsing dots with staggered animation.
 */

export interface StreamingIndicatorProps {
  /** Optional label text next to the dots. */
  label?: string;
}

export default function StreamingIndicator({
  label = "Thinking",
}: StreamingIndicatorProps) {
  return (
    <div
      className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400"
      data-testid="streaming-indicator"
      role="status"
      aria-label={label}
    >
      <span className="flex gap-1" aria-hidden="true">
        <span className="h-2 w-2 rounded-full bg-primary-500 animate-bounce [animation-delay:0ms]" />
        <span className="h-2 w-2 rounded-full bg-primary-500 animate-bounce [animation-delay:150ms]" />
        <span className="h-2 w-2 rounded-full bg-primary-500 animate-bounce [animation-delay:300ms]" />
      </span>
      <span>{label}...</span>
    </div>
  );
}

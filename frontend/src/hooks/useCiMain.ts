import { useEffect, useState } from "react";

import { getCiMain, type CiMain } from "@/services/api/ciRuns";

/**
 * DEV-85 — what the cockpit's watch last saw on the project's main branch, asked again on the watch's own schedule.
 * A failed question shows nothing rather than an old answer: a mark that stays red after a fix is worse than none.
 */
export function useCiMain(slug: string | null | undefined): CiMain | null {
  const [main, setMain] = useState<CiMain | null>(null);
  const [everyMs, setEveryMs] = useState(60_000);

  useEffect(() => {
    setMain(null);
    if (!slug) return;
    let cancelled = false;
    // `Promise.resolve().then` — a missing function throws synchronously and would take the whole sidebar down.
    const ask = () =>
      Promise.resolve()
        .then(() => getCiMain(slug))
        .then((m) => {
          if (cancelled) return;
          setMain(m);
          setEveryMs(m.refresh_seconds * 1000);
        })
        .catch(() => {
          if (!cancelled) setMain(null);
        });
    void ask();
    const timer = setInterval(() => void ask(), everyMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [slug, everyMs]);

  return main;
}

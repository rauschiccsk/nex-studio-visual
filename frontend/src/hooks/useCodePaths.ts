import { useEffect, useState } from "react";

import { getCodeFiles } from "@/services/api/nsCode";

/** How long the list is kept for every text that asks — a conversation renders many messages at once. */
const KEEP_MS = 60_000;

const kept = new Map<string, { at: number; paths: ReadonlySet<string> }>();
const asking = new Map<string, Promise<ReadonlySet<string> | null>>();

/** Forget the kept lists — for tests. @internal — exported for its test (DEV-155). */
export function forgetCodePaths(): void {
  kept.clear();
  asking.clear();
}

function ask(slug: string): Promise<ReadonlySet<string> | null> {
  const fresh = kept.get(slug);
  if (fresh && Date.now() - fresh.at < KEEP_MS) return Promise.resolve(fresh.paths);
  let pending = asking.get(slug);
  if (!pending) {
    pending = Promise.resolve()
      .then(() => getCodeFiles(slug))
      .then((f) => {
        const paths: ReadonlySet<string> = new Set(f.files);
        kept.set(slug, { at: Date.now(), paths });
        return paths;
      })
      .catch(() => null) // no list (no git, no access) — the text simply stays text
      .finally(() => asking.delete(slug));
    asking.set(slug, pending);
  }
  return pending;
}

/**
 * DEV-88 — the files NS Code shows for the project, for turning the paths a text names into links. One question for
 * every text rendered at the same time, kept for a minute; `null` until it arrives or when it cannot.
 */
export function useCodePaths(slug: string | null | undefined): ReadonlySet<string> | null {
  const [paths, setPaths] = useState<ReadonlySet<string> | null>(() => (slug ? (kept.get(slug)?.paths ?? null) : null));
  useEffect(() => {
    if (!slug) {
      setPaths(null);
      return;
    }
    let cancelled = false;
    void ask(slug).then((p) => !cancelled && setPaths(p));
    return () => {
      cancelled = true;
    };
  }, [slug]);
  return paths;
}

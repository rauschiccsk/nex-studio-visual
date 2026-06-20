/**
 * Aktualizácie — per-version, user-facing changelog ("Čo je nové").
 *
 * Fetches ``GET /api/v1/release-notes`` (public, no auth) and hands the result
 * to the unified ``<ReleaseNotes>`` renderer from ``nex-shared`` (E1
 * unification — the changelog look lives in nex-shared like the chrome,
 * Director-approved). This page owns only the data layer (fetch + loading /
 * error state); all presentation is the shared component's responsibility.
 */

import { useCallback, useEffect, useState } from "react";
import { ReleaseNotes } from "nex-shared";

import { ApiError } from "@/services/api";
import { listReleaseNotes, type ReleaseNote } from "@/services/api/releaseNotes";

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
    <ReleaseNotes
      notes={notes}
      loading={loading}
      error={error || null}
      onDismissError={() => setError("")}
      appName="NEX Studio"
    />
  );
}

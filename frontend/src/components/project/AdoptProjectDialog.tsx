// AdoptProjectDialog — prevzatie existujúceho projektu jedným výberom (ICCINT-85).
//
// Prevzatie sa dovtedy robilo formulárom pre ZAKLADANIE: manažér musel vedieť a ručne prepísať porty,
// adresu repozitára, typ aj spôsob prihlasovania. Keď sa v niečom pomýlil, evidencia začala tvrdiť niečo
// iné, než je na disku — a projekt beží podľa disku, nie podľa evidencie. Pritom to všetko na disku UŽ JE.
//
// Director 09.09.2026: „zadám len názov projektu — napríklad nex-manager — a všetko ostatné urobí systém.“
//
// Dva kroky, a ten druhý je dôležitý: najprv sa ukáže, ČO systém našiel a ODKIAĽ, a až potom sa preberá.
// Prevzatie je zápis do evidencie, ktorý má sedieť s realitou — manažér to musí vidieť pred potvrdením,
// nie sa to dozvedieť potom.

import { useEffect, useState } from "react";
import { AlertTriangle, HardDriveDownload, Loader2 } from "lucide-react";

import {
  createProjectApi,
  listAdoptableApi,
  previewAdoptionApi,
  type AdoptableCandidate,
  type AdoptionPreview,
} from "@/services/api/projects";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { useAuthStore } from "@/store/authStore";
import ErrorNote from "@/components/common/ErrorNote";

interface Props {
  open: boolean;
  onClose: () => void;
  onAdopted: (slug: string) => void;
}

export default function AdoptProjectDialog({ open, onClose, onAdopted }: Props) {
  const user = useAuthStore((s) => s.user);
  const [candidates, setCandidates] = useState<AdoptableCandidate[] | null>(null);
  const [chosen, setChosen] = useState<string>("");
  const [preview, setPreview] = useState<AdoptionPreview | null>(null);
  const [authMode, setAuthMode] = useState<"token" | "password">("token");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  useEffect(() => {
    if (!open) return;
    setCandidates(null);
    setChosen("");
    setPreview(null);
    setError(null);
    listAdoptableApi()
      .then(setCandidates)
      .catch((e: unknown) => {
        setCandidates([]);
        setError(humanizeApiError(e, "Nepodarilo sa načítať priečinky na disku"));
      });
  }, [open]);

  // Prehliadka sa načíta hneď po výbere. Manažér tak nemusí klikať dvakrát, aby videl, čo ho čaká.
  useEffect(() => {
    if (!chosen) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    setPreview(null);
    setError(null);
    previewAdoptionApi(chosen)
      .then((p) => {
        if (!cancelled) setPreview(p);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(humanizeApiError(e, "Priečinok sa nepodarilo prečítať"));
      });
    return () => {
      cancelled = true;
    };
  }, [chosen]);

  if (!open) return null;

  const adopt = async () => {
    if (!preview || busy) return;
    setBusy(true);
    setError(null);
    try {
      await createProjectApi({
        name: preview.name || preview.slug,
        slug: preview.slug,
        type: "standard",
        auth_mode: authMode,
        description: preview.description || `Prevzatý projekt ${preview.slug}.`,
        backend_port: preview.backend_port,
        frontend_port: preview.frontend_port,
        db_port: preview.db_port,
        repo_url: preview.repo_url,
        source_path: preview.source_path,
        // Vlastníkom prevzatého projektu je ten, kto ho preberá — rovnako ako pri zakladaní.
        created_by: user?.id ?? "",
      });
      onAdopted(preview.slug);
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Prevzatie projektu zlyhalo"));
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-xl rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-5">
        <div className="mb-1 flex items-center gap-2">
          <HardDriveDownload className="h-4 w-4 text-[var(--color-text-muted)]" aria-hidden="true" />
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">Prevziať existujúci projekt</h2>
        </div>
        <p className="mb-4 text-xs text-[var(--color-text-muted)]">
          Vyber priečinok, ktorý na disku už je. Ostatné si NEX Studio prečíta samo — nič sa v projekte
          neprepíše, ostane si vlastné pravidlá aj históriu.
        </p>

        <label className="mb-1 block text-xs text-[var(--color-text-muted)]" htmlFor="adopt-slug">
          Priečinok
        </label>
        <select
          id="adopt-slug"
          value={chosen}
          onChange={(e) => setChosen(e.target.value)}
          disabled={busy || candidates === null}
          className="mb-4 w-full rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-canvas)] px-3 py-2 text-sm"
        >
          <option value="">
            {candidates === null
              ? "Načítavam…"
              : candidates.length === 0
                ? "Na disku nie je nič, čo by sa dalo prevziať"
                : "Vyber priečinok…"}
          </option>
          {(candidates ?? []).map((c) => (
            <option key={c.slug} value={c.slug}>
              {c.name ? `${c.name} (${c.slug})` : c.slug}
            </option>
          ))}
        </select>

        {chosen && !preview && !error && (
          <div className="mb-4 flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Čítam priečinok…
          </div>
        )}

        {preview && (
          <div className="mb-4 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-4">
            <div className="mb-2 text-[10px] uppercase tracking-widest text-[var(--color-text-muted)]">
              Čo som našiel
            </div>
            <dl className="space-y-1 text-xs">
              <div className="flex gap-2">
                <dt className="w-28 shrink-0 text-[var(--color-text-muted)]">Názov</dt>
                <dd className="font-medium">{preview.name ?? "—"}</dd>
              </div>
              <div className="flex gap-2">
                <dt className="w-28 shrink-0 text-[var(--color-text-muted)]">Repozitár</dt>
                <dd className="break-all">{preview.repo_url ?? "—"}</dd>
              </div>
              <div className="flex gap-2">
                <dt className="w-28 shrink-0 text-[var(--color-text-muted)]">Posledná verzia</dt>
                <dd>
                  {preview.latest_version ?? "—"}
                  <span className="text-[var(--color-text-muted)]">
                    {" "}
                    — prvú verziu si po prevzatí založíš sám
                  </span>
                </dd>
              </div>
              <div className="flex gap-2">
                <dt className="w-28 shrink-0 text-[var(--color-text-muted)]">Porty</dt>
                <dd>
                  {preview.backend_port ?? "—"} / {preview.frontend_port ?? "—"} / {preview.db_port ?? "—"}
                  <span className="text-[var(--color-text-muted)]"> (backend / frontend / databáza)</span>
                </dd>
              </div>
            </dl>

            {/* Čo sa prečítať nedalo. Uhádnutý údaj by vyzeral ako zistený — a evidencia, ktorá tvrdí
                niečo iné než disk, je horšia než prázdne políčko. */}
            {preview.unresolved.length > 0 && (
              <div className="mt-3 flex gap-2 text-[11px] text-[var(--color-status-warning)]">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <ul className="space-y-0.5">
                  {preview.unresolved.map((u) => (
                    <li key={u}>{u}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {preview && (
          <>
            <label className="mb-1 block text-xs text-[var(--color-text-muted)]" htmlFor="adopt-auth">
              Spôsob prihlasovania (z disku sa nedá zistiť)
            </label>
            <select
              id="adopt-auth"
              value={authMode}
              onChange={(e) => setAuthMode(e.target.value as "token" | "password")}
              disabled={busy}
              className="mb-4 w-full rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-canvas)] px-3 py-2 text-sm"
            >
              <option value="token">Token</option>
              <option value="password">Meno a heslo</option>
            </select>
          </>
        )}

        <ErrorNote error={error} className="mb-3" />

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-lg px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
          >
            Zrušiť
          </button>
          <button
            type="button"
            onClick={adopt}
            disabled={!preview || busy}
            className="rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Preberám…" : "Prevziať projekt"}
          </button>
        </div>
      </div>
    </div>
  );
}

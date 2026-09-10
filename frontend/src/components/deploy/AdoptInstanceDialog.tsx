/**
 * Prevziať ručne písanú inštaláciu — z kokpitu, nie z terminálu (ICCINT-102).
 *
 * **Čo tomu predchádzalo.** Keď NEX Studio nájde v cieľovom priečinku nasadenie, ktoré samo
 * nevygenerovalo, odmietne doň zapisovať. Poistka je správna — na ANDROSe je trinásť ručne písaných
 * inštalácií vrátane ostrej pošty MÁGERSTAVU a spoločného Traefiku. Jediná cesta ďalej však viedla
 * cez terminál, a Director 10.09.2026 povedal jasne: *„Také terminálové príkazy nie sú pre mňa
 * riešenie.“* Ekosystém má zvládnuť celý priebeh vrátane nasadenia.
 *
 * **Čo tento dialóg robí inak než ten príkaz.** Terminálový `--dry-run` ukázal, čo sa prepíše, len
 * tomu, kto vie napísať príkaz — a po samotnom prevzatí nezostala v produkte žiadna stopa. Tu:
 *
 * 1. **najprv ukáž, potom rob** — vidno priečinok, čo sa odloží, čoho sa to nedotkne a **čo z toho
 *    priečinka práve beží**;
 * 2. **odpísať frázu**, nie kliknúť — omylom sa to stať nedá;
 * 3. ručné súbory sa **odložia**, nie zmažú — dôkaz zostáva a krok je vratný;
 * 4. zapíše sa **kto, kedy a čo** prevzal;
 * 5. **tajomstvá sa zachovajú** — databáza v tom priečinku beží so starým heslom.
 *
 * ⚠️ Tlačidlo „Nasadiť“ túto možnosť nemá a nedostane ju. Prevzatie je oddelené rozhodnutie
 * s vlastnou obrazovkou; poistka nad bežným nasadením zostáva nedotknutá.
 */

import { useEffect, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";

import { getAdoptionPreview, adoptInstance, type AdoptionPreview } from "@/services/api/deploy";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";

interface Props {
  customerId: string;
  customerName: string;
  environment: string;
  versionNumber: string;
  onClose: () => void;
  onAdopted: (message: string) => void;
}

export default function AdoptInstanceDialog({
  customerId,
  customerName,
  environment,
  versionNumber,
  onClose,
  onAdopted,
}: Props) {
  const [preview, setPreview] = useState<AdoptionPreview | null>(null);
  const [loadError, setLoadError] = useState<HumanError | null>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAdoptionPreview(customerId, environment)
      .then((p) => { if (!cancelled) setPreview(p); })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(humanizeApiError(err, "Náhľad prevzatia sa nepodarilo načítať"));
      });
    return () => { cancelled = true; };
  }, [customerId, environment]);

  const potvrdene = preview !== null && typed.trim() === preview.confirmation_phrase;

  async function adopt() {
    if (!preview || !potvrdene) return;
    setBusy(true);
    setError(null);
    try {
      const result = await adoptInstance(customerId, {
        version_number: versionNumber,
        environment,
        confirm: typed.trim(),
      });
      onAdopted(
        result.ok
          ? `Inštalácia prevzatá a verzia ${versionNumber} nasadená.`
          : `Prevzatie prebehlo, ale nasadenie zlyhalo${result.event.detail ? ` — ${result.event.detail}` : ""}.`,
      );
      onClose();
    } catch (err) {
      setError(humanizeApiError(err, "Prevzatie zlyhalo"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-2xl rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-5 shadow-xl">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
          Prevziať inštaláciu — {customerName}
        </h2>

        {loadError && <ErrorNote error={loadError} className="mt-4" />}

        {!preview && !loadError && (
          <p className="mt-4 flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Zisťujem, čo je v tom priečinku…
          </p>
        )}

        {preview && !preview.exists && (
          <p className="mt-4 text-sm text-[var(--color-text-secondary)]">
            Priečinok <span className="font-mono">{preview.instance_dir}</span> neexistuje — preberať
            niet čo. Použi bežné <b>Nasadiť</b>; vytvorí sa sám.
          </p>
        )}

        {preview && preview.exists && preview.already_ours && (
          <p className="mt-4 text-sm text-[var(--color-text-secondary)]">
            Priečinok <span className="font-mono">{preview.instance_dir}</span> už NEX Studio spravuje —
            preberať niet čo. Použi bežné <b>Nasadiť</b>.
          </p>
        )}

        {preview && preview.exists && !preview.already_ours && (
          <>
            <div className="mt-4 rounded-lg border border-[var(--color-status-warning)]/50 bg-[var(--color-status-warning)]/10 p-3">
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 text-[var(--color-status-warning)] mt-0.5 shrink-0" />
                <div className="text-xs text-[var(--color-text-secondary)] space-y-1">
                  <p>
                    V priečinku <span className="font-mono">{preview.instance_dir}</span> je nasadenie,
                    ktoré NEX Studio nevytvorilo — je písané ručne.
                  </p>
                  {preview.running_containers.length > 0 && (
                    <p>
                      <b>Práve z neho beží:</b>{" "}
                      <span className="font-mono">{preview.running_containers.join(", ")}</span>. Nasadenie
                      ich nahradí verziou {versionNumber}.
                    </p>
                  )}
                </div>
              </div>
            </div>

            <dl className="mt-4 space-y-2 text-xs">
              <div>
                <dt className="font-medium text-[var(--color-text-secondary)]">Odloží sa bokom (nezmaže):</dt>
                <dd className="mt-0.5 font-mono text-[var(--color-text-muted)]">
                  {preview.set_aside.length === 0
                    ? "nič"
                    : preview.set_aside.map(([z, na]) => `${z} → ${na}`).join(", ")}
                </dd>
              </div>
              <div>
                <dt className="font-medium text-[var(--color-text-secondary)]">Nedotkne sa:</dt>
                <dd className="mt-0.5 font-mono text-[var(--color-text-muted)]">
                  {preview.untouched.length === 0 ? "nič iné tam nie je" : preview.untouched.join(", ")}
                </dd>
              </div>
              <div>
                <dt className="font-medium text-[var(--color-text-secondary)]">Prihlasovacie údaje a dáta:</dt>
                <dd className="mt-0.5 text-[var(--color-text-muted)]">
                  zachovajú sa — databáza v tom priečinku beží so svojím heslom
                </dd>
              </div>
            </dl>

            <label className="mt-4 block text-xs font-medium text-[var(--color-text-secondary)]" htmlFor="adopt-confirm">
              Na potvrdenie odpíš <span className="font-mono">{preview.confirmation_phrase}</span>
            </label>
            <input
              id="adopt-confirm"
              spellCheck={false}
              autoComplete="off"
              className="mt-1 w-full rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-3 py-2 font-mono text-sm text-[var(--color-text-primary)] focus:border-primary-500 focus:outline-none"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
            />
            <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
              Odpísanie je poistka proti prevzatiu nesprávnej inštalácie — priečinok sa u troch zákazníkov
              volá rovnako. Po prevzatí sa tento priečinok stane spravovaným a ďalšie nasadenia doň pôjdu
              jedným kliknutím.
            </p>

            <ErrorNote error={error} className="mt-3" />
          </>
        )}

        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50 transition-colors"
          >
            Zrušiť
          </button>
          {preview && preview.exists && !preview.already_ours && (
            <button
              type="button"
              onClick={adopt}
              disabled={busy || !potvrdene}
              title={potvrdene ? undefined : `Najprv odpíš „${preview.confirmation_phrase}“.`}
              className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {busy ? "Preberám…" : `Prevziať a nasadiť ${versionNumber}`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

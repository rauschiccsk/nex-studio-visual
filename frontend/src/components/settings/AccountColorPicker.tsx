import { useEffect, useState } from "react";
import { getAccentColorsApi, updateMyProfileApi } from "@/services/api/auth";
import { useAuthStore } from "@/store/authStore";

/** DEV-320: the person picks their account's colour from the server's palette — every window of this account shows
 *  it, so windows of different accounts side by side are not mixed up. */
export default function AccountColorPicker() {
  const user = useAuthStore((s) => s.user);
  const [palette, setPalette] = useState<string[]>([]);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAccentColorsApi()
      .then((colours) => {
        if (!cancelled) setPalette(colours);
      })
      .catch(() => {
        if (!cancelled) setError("Ponuku farieb sa nepodarilo načítať.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const choose = async (colour: string) => {
    setSaving(colour);
    setError(null);
    try {
      const updated = await updateMyProfileApi({ accent_color: colour });
      useAuthStore.setState({ user: updated });
    } catch {
      setError("Farbu sa nepodarilo uložiť. Skús to znova.");
    } finally {
      setSaving(null);
    }
  };

  return (
    <section className="mt-6 space-y-2" aria-labelledby="account-colour-title">
      <h3 id="account-colour-title" className="text-sm font-semibold text-[var(--color-text-primary)]">
        Farba konta
      </h3>
      <p className="text-xs text-[var(--color-text-muted)]">
        Každé okno kokpitu tohto konta ju ukáže v pruhu hore a pri mene — okná viacerých kont vedľa seba si tak
        nepomýliš.
      </p>
      <div className="flex flex-wrap gap-2">
        {palette.map((colour) => {
          const chosen = user?.accent_color === colour;
          return (
            <button
              key={colour}
              type="button"
              aria-label={`Farba ${colour}`}
              aria-pressed={chosen}
              disabled={saving !== null}
              onClick={() => void choose(colour)}
              className={`h-8 w-8 rounded-full border-2 transition ${
                chosen ? "border-[var(--color-text-primary)] ring-2 ring-offset-2" : "border-transparent"
              }`}
              style={{ backgroundColor: colour }}
            />
          );
        })}
      </div>
      {error && (
        <p role="alert" className="text-xs text-[var(--color-state-error-fg)]">
          {error}
        </p>
      )}
    </section>
  );
}

import { useState, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { LoginForm, type LoginCreds } from "nex-shared";
import { useAuthStore } from "@/store/authStore";
import { useBackendVersion } from "@/hooks/useBackendVersion";
import { verziaDoPanela } from "@/lib/verziaDoPanela";

export default function LoginPage() {
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const verziaBackendu = useBackendVersion();
  const login = useAuthStore((s) => s.login);
  const token = useAuthStore((s) => s.token);
  const navigate = useNavigate();
  const location = useLocation();

  // Redirect if already logged in
  useEffect(() => {
    if (token) {
      const from = (location.state as { from?: { pathname: string } })?.from?.pathname ?? "/";
      navigate(from, { replace: true });
    }
  }, [token, navigate, location.state]);

  const handleSubmit = async ({ username, password }: LoginCreds) => {
    setError("");
    setLoading(true);
    try {
      await login(username, password);
      const from = (location.state as { from?: { pathname: string } })?.from?.pathname ?? "/";
      navigate(from, { replace: true });
    } catch {
      setError("Nesprávne prihlasovacie údaje.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen bg-[var(--color-canvas)]">
      <div className="w-full max-w-sm px-4">
        {/* Branding */}
        <div className="mb-8 text-center">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-primary-600 text-white font-bold text-xl mb-4 shadow-lg shadow-primary-600/30">
            N
          </div>
          <h1 className="text-2xl font-bold text-primary-400 tracking-tight">NEX Studio</h1>
          <p className="mt-1 text-sm text-[var(--color-text-muted)]">Prihláste sa na svoj účet</p>
        </div>

        {/* Card — the form is the shared <LoginForm> (E1 Phase C); branding +
            card chrome + version footer + the already-logged-in redirect stay here. */}
        <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-8 shadow-2xl">
          <LoginForm
            fieldLabel="username"
            onSubmit={handleSubmit}
            loading={loading}
            error={error || null}
            onChange={() => setError("")}
            autoFocus
            identityPlaceholder="napr. zoltan"
          />
        </div>

        {/* ICCINT-128: to isté pravidlo ako v bočnom paneli. Prihlasovacia obrazovka dovtedy písala
            číslo vpečené do frontendu ako verziu celej aplikácie — a práve na nej ho vidí každý,
            kto ešte nie je vnútri. */}
        <p className="mt-4 text-center text-[11px] text-[var(--color-text-muted)]">
          NEX Studio {verziaDoPanela(verziaBackendu, import.meta.env.VITE_APP_VERSION)} · ICC
        </p>
      </div>
    </div>
  );
}

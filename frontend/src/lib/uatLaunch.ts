// v4.0.30 — a token app in UAT opens signed in: the cockpit mints a short-lived launch ticket server-side for the person
// who clicked and the app opens with it. DEV-391 — one way for every place that offers it: „Spustiť“ on the UAT page
// and „Otvoriť“ on the Inštalácie page.
import { ApiError } from "@/services/api";
import { uatLaunch } from "@/services/api/deploy";
import { humanizeApiError } from "@/services/apiError";

/** Open the app; `null` when it opened, otherwise the sentence why not — the endpoint's own 400/404 sentences are plain
 *  Slovak with a next step („NEX Manager tejto aplikácie nepozná meno …“), shown as they are. */
export async function openUatApp(customerId: string, projectSlug: string): Promise<string | null> {
  try {
    const { launch_url } = await uatLaunch(customerId, projectSlug);
    window.open(launch_url, "_blank", "noopener");
    return null;
  } catch (err) {
    const curated =
      err instanceof ApiError &&
      (err.status === 400 || err.status === 404) &&
      typeof err.message === "string" &&
      err.message &&
      err.message !== "[object Object]"
        ? err.message
        : null;
    return (
      curated ??
      (err instanceof ApiError ? humanizeApiError(err, "Spustenie zlyhalo").message : "Sieťová chyba pri spúšťaní.")
    );
  }
}

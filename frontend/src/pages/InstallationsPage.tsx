// DEV-378 — „Inštalácie“: every installation the cockpit deployed, by customer, UAT and PROD side by side.
//
// 13.11.2026 the Director: „mám už veľa ikonov na spúšťanie rôznych aplikácii … Je to len niekoľko zákazníkov a už
// strácam prehľad … Navrhni mi prosím nejaké riešenie čo bude prehľadné aj pri 50+ zákazníkov.“ On the page as
// explained (by customer; a version, whether it answers and „Otvoriť“; search and filters; from the cockpit's deploy
// records): „Súhlasím, ideme to robiť.“ The list comes from the server (GET /installations); whether an installation
// lives is asked a few at a time (SETTINGS.INSTALLATION_STATUS_CONCURRENCY) — the server asks the installation at its
// own address and shares the answer. Nothing here deploys or deletes.
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { SETTINGS } from "@/config/settings";
import {
  getInstallationStatusApi,
  listInstallationsApi,
  type Environment,
  type InstallationApp,
  type InstallationCustomer,
  type InstallationEnvironment,
  type InstallationStatus,
} from "@/services/api/installations";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";
import { useActiveContextStore } from "@/store/activeContextStore";

const ENVIRONMENTS: Environment[] = ["uat", "prod"];
const ENV_LABEL: Record<Environment, string> = { uat: "UAT", prod: "PROD" };

type Asked = InstallationStatus | "asking" | "unknown";

function statusKey(customerId: string, environment: Environment): string {
  return `${customerId}:${environment}`;
}

/** Accents and case do not matter in the search — „mager“ finds MÁGERSTAV. */
function plain(text: string): string {
  return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

/** Asks every deployed installation whether it lives — `limit` at a time, in the order of the list. */
function useStatuses(groups: InstallationCustomer[] | null, limit: number): Record<string, Asked> {
  const [statuses, setStatuses] = useState<Record<string, Asked>>({});
  useEffect(() => {
    if (!groups) return;
    const queue = groups.flatMap((g) =>
      g.apps.flatMap((app) => ENVIRONMENTS.filter((env) => app[env]).map((env) => [app.customer_id, env] as const)),
    );
    let live = true;
    setStatuses(Object.fromEntries(queue.map(([id, env]) => [statusKey(id, env), "asking" as Asked])));
    const work = async () => {
      for (let next = queue.shift(); next && live; next = queue.shift()) {
        const [id, env] = next;
        let answer: Asked;
        try {
          answer = await getInstallationStatusApi(id, env);
        } catch {
          answer = "unknown";
        }
        if (live) setStatuses((s) => ({ ...s, [statusKey(id, env)]: answer }));
      }
    };
    void Promise.all(Array.from({ length: Math.max(1, limit) }, work));
    return () => {
      live = false;
    };
  }, [groups, limit]);
  return statuses;
}

function StatusChip({ asked }: { asked: Asked | undefined }) {
  if (!asked || asked === "asking") {
    return <span className="text-xs text-[var(--color-text-muted)]">zisťujem…</span>;
  }
  if (asked === "unknown") {
    return <span className="text-xs text-[var(--color-text-muted)]">stav nezistený</span>;
  }
  if (asked.state === "up") {
    return <span className="text-xs font-medium text-[var(--color-status-success)]">● beží</span>;
  }
  if (asked.state === "answers") {
    return (
      <span className="text-xs font-medium text-[var(--color-status-warning)]" title={asked.detail}>
        ◐ odpovedá
      </span>
    );
  }
  return (
    <span className="text-xs font-medium text-[var(--color-status-error)]">
      ○ neodpovedá{asked.detail ? ` — ${asked.detail}` : ""}
    </span>
  );
}

function OpenLinks({ installation }: { installation: InstallationEnvironment }) {
  const links = installation.links?.length ? installation.links : [{ label: "", url: installation.url }];
  return (
    <span className="flex flex-wrap gap-1.5">
      {links.map((link) => (
        <a
          key={link.url}
          href={link.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center rounded-md border border-[var(--color-border-default)] px-2 py-0.5 text-xs font-medium text-[var(--color-accent-primary)] hover:bg-[var(--color-surface-hover)] pointer-coarse:min-h-11"
        >
          {link.label ? `Otvoriť: ${link.label}` : "Otvoriť"}
        </a>
      ))}
    </span>
  );
}

function EnvironmentCell({ app, environment, asked }: { app: InstallationApp; environment: Environment; asked?: Asked }) {
  const installation = app[environment];
  if (!installation) {
    return <span className="text-xs text-[var(--color-text-muted)]">— ešte nenasadené</span>;
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="font-mono text-xs text-[var(--color-text-primary)]">{installation.version}</span>
      <StatusChip asked={asked} />
      <OpenLinks installation={installation} />
      {installation.last_attempt_failed && (
        <span className="text-xs text-[var(--color-status-warning)]">posledný pokus o nasadenie zlyhal</span>
      )}
    </div>
  );
}

export default function InstallationsPage() {
  const navigate = useNavigate();
  const setSelectedProject = useActiveContextStore((s) => s.setSelectedProject);
  const [groups, setGroups] = useState<InstallationCustomer[] | null>(null);
  const [error, setError] = useState<HumanError | null>(null);
  const [query, setQuery] = useState("");
  const [environment, setEnvironment] = useState<"all" | Environment>("all");
  const [app, setApp] = useState("");

  useEffect(() => {
    listInstallationsApi()
      .then(setGroups)
      .catch((err: unknown) => setError(humanizeApiError(err, "Inštalácie sa nepodarilo načítať")));
  }, []);

  const statuses = useStatuses(groups, SETTINGS.INSTALLATION_STATUS_CONCURRENCY);
  const appNames = useMemo(
    () => [...new Set((groups ?? []).flatMap((g) => g.apps.map((a) => a.project_name)))].sort((a, b) => a.localeCompare(b, "sk")),
    [groups],
  );
  const shownEnvironments = environment === "all" ? ENVIRONMENTS : [environment];
  const shown = useMemo(() => {
    const words = plain(query.trim());
    return (groups ?? [])
      .map((g) => ({
        ...g,
        apps: g.apps.filter(
          (a) =>
            (!app || a.project_name === app) &&
            (environment === "all" || a[environment]) &&
            (!words || plain(g.name).includes(words) || plain(a.project_name).includes(words)),
        ),
      }))
      .filter((g) => g.apps.length > 0);
  }, [groups, query, environment, app]);

  const openDeployPage = (target: InstallationApp) => {
    setSelectedProject({ slug: target.project_slug, name: target.project_name });
    navigate("/uat");
  };

  return (
    <div className="p-4 md:p-6">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-base font-bold text-[var(--color-text-primary)]">Inštalácie</h1>
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Hľadať zákazníka alebo aplikáciu…"
            spellCheck={false}
            aria-label="Hľadať zákazníka alebo aplikáciu"
            className="w-full rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-canvas)] px-3 py-1.5 text-xs sm:w-64 text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:border-primary-500 focus:outline-none"
          />
          <label className="flex min-w-0 max-w-full items-center gap-1 text-xs text-[var(--color-text-secondary)]">
            Prostredie:
            <select
              value={environment}
              onChange={(e) => setEnvironment(e.target.value as "all" | Environment)}
              className="min-w-0 max-w-full rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-canvas)] px-2 py-1 text-xs text-[var(--color-text-primary)]"
            >
              <option value="all">Všetky</option>
              <option value="uat">UAT</option>
              <option value="prod">PROD</option>
            </select>
          </label>
          <label className="flex min-w-0 max-w-full items-center gap-1 text-xs text-[var(--color-text-secondary)]">
            Aplikácia:
            <select
              value={app}
              onChange={(e) => setApp(e.target.value)}
              className="min-w-0 max-w-full rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-canvas)] px-2 py-1 text-xs text-[var(--color-text-primary)]"
            >
              <option value="">Všetky</option>
              {appNames.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      <p className="mb-4 text-xs text-[var(--color-text-muted)]">
        Každá aplikácia, ktorú kokpit nasadil, podľa zákazníka. „Otvoriť“ ju otvorí v novom okne; meno aplikácie vedie
        na jej stránku nasadenia.
      </p>

      <ErrorNote error={error} />
      {groups === null && !error && <p className="text-xs text-[var(--color-text-muted)]">Načítavam inštalácie…</p>}
      {groups !== null && groups.length === 0 && (
        <p className="text-xs text-[var(--color-text-muted)]">Kokpit zatiaľ nenasadil žiadnu inštaláciu.</p>
      )}
      {groups !== null && groups.length > 0 && shown.length === 0 && (
        <p className="text-xs text-[var(--color-text-muted)]">Nič nezodpovedá hľadaniu.</p>
      )}

      <div className="flex flex-col gap-5">
        {shown.map((group) => (
          <section key={group.key} aria-label={group.name}>
            <h2 className="mb-1.5 text-sm font-semibold text-[var(--color-text-primary)]">{group.name}</h2>
            <div className="overflow-x-auto rounded-lg border border-[var(--color-border-default)]">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-[var(--color-border-default)] text-xs text-[var(--color-text-muted)]">
                    <th className="px-3 py-1.5 font-medium">Aplikácia</th>
                    {shownEnvironments.map((env) => (
                      <th key={env} className="px-3 py-1.5 font-medium">
                        {ENV_LABEL[env]}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {group.apps.map((target) => (
                    <tr
                      key={`${target.customer_id}`}
                      className="border-b border-[var(--color-border-default)] last:border-b-0 align-top"
                    >
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => openDeployPage(target)}
                          className="text-left text-sm font-medium text-[var(--color-text-primary)] hover:text-[var(--color-accent-primary)] hover:underline"
                        >
                          {target.project_name}
                        </button>
                      </td>
                      {shownEnvironments.map((env) => (
                        <td key={env} className="px-3 py-2">
                          <EnvironmentCell
                            app={target}
                            environment={env}
                            asked={statuses[statusKey(target.customer_id, env)]}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

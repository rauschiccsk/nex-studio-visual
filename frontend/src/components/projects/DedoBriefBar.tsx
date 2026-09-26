// DedoBriefBar — zadanie od Deda pre prácu, ktorá sa ešte NEZAČALA (ICCINT-152).
//
// Dvojička k DedoProposalBar, ktorý to isté robí na obrazovke bežiacej stavby. Rozdiel je jediný a je to
// celý dôvod, prečo tento panel vznikol: tam už stavba beží, tu ešte nie — a práve vtedy je zadanie
// najpotrebnejšie.
//
// 25.09.2026 prestalo na MÁGERSTAVE fungovať spúšťanie NEX Inboxu z NEX Managera. Oprava patrila do
// NEX Inboxu a Director požiadal: „zapíš to zadanie do kokpitu ako návrh." Nešlo to — Dedove dvere vedia
// položiť návrh len na rozbehnutú stavbu, teda práve vtedy, keď už zadanie netreba. Text mu Dedo musel
// podať do ruky, aby ho pri spúšťaní rýchlej opravy vložil. Presne tomu mal ten mechanizmus zabrániť.
//
// ČO SA NEMENÍ: kto rozhoduje. Dedo text POLOŽÍ; verzia vzniká až kliknutím Manažéra, pod jeho účtom,
// tou istou cestou, akou by formulár vyplnil sám. Panel je preto editovateľný a má dve tlačidlá — spustiť
// alebo zamietnuť. „Manažér rozhoduje" a „Manažér prepisuje" nie je to isté; odpadá len to druhé.
//
// ČO VIDÍ, TO SA STANE: obe tlačidlá nesú identifikátor zadania, ktoré má na obrazovke. Keby Dedo medzitým
// napísal novšie, server odmietne (409) namiesto zámeny — a panel vtedy načíta to nové a povie prečo.

import { useEffect, useState } from "react";
import { Lightbulb, Send, X } from "lucide-react";

import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import { useDraft, draftKey } from "@/hooks/useDraft";
import { ApiError } from "@/services/api";
import {
  getProjectDedoProposalApi,
  rejectProjectDedoProposalApi,
  sendProjectDedoProposalApi,
  type DedoProjectProposal,
} from "@/services/api/projects";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";
import { WARNING_CHROME } from "@/components/common/WarningActionBar";

// Čo tlačidlo spraví, povedané ako dôsledok — nie ako sloveso enginu. Manažér si akciu nevyberá; zadanie
// ju nesie a panel mu len povie, čo stlačenie znamená.
const ACTION_COPY: Record<string, { button: string; effect: string }> = {
  fast_fix: {
    button: "Spustiť rýchlu opravu",
    effect:
      "Vytvorí sa nová opravná verzia a hneď sa rozbehne odľahčená linka (Príprava → Programovanie → Vydanie).",
  },
  new_version: {
    button: "Založiť novú verziu",
    effect: "Verzia vznikne ako koncept s týmto zadaním v popise. Nič sa nerozbehne — spustíš ju, keď budeš chcieť.",
  },
};

const FALLBACK_COPY = {
  button: "Použiť zadanie",
  effect: "Zo zadania vznikne nová verzia.",
};

interface Props {
  projectId: string;
  proposal: DedoProjectProposal | null;
  /** Nahradí zadanie tým, čo hovorí server — alebo `null`, keď už žiadne nie je. */
  onProposal: (proposal: DedoProjectProposal | null) => void;
  /** Kam ísť, keď z zadania vznikla verzia. */
  onVersion: (versionId: string, started: boolean) => void | Promise<void>;
}

export default function DedoBriefBar({ projectId, proposal, onProposal, onVersion }: Props) {
  const proposalId = proposal?.id ?? null;

  // Jeho úpravy prežijú odchod z obrazovky. Bez toho by sa vrátil k nedotknutému originálu a odoslal
  // znenie, proti ktorému sa už raz rozhodol, v presvedčení, že je jeho.
  const draft = useDraft(draftKey(`zadanie.${proposalId ?? "none"}`, projectId));
  const text = draft.text || (proposal?.content ?? "");
  const setText = draft.setText;
  const [busy, setBusy] = useState<"send" | "reject" | null>(null);
  const [error, setError] = useState<HumanError | null>(null);
  // Prečo sa obrazovka zmenila pod rukami. Držané oddelene od `error`: po odmietnutí nasleduje načítanie
  // nového zadania, ktoré `error` vyčistí — a vysvetlenie by zmizlo v tej istej chvíli ako dôvod.
  const [staleNotice, setStaleNotice] = useState<string | null>(null);
  // Háčiky musia bežať PRED skorým návratom nižšie — háčik za podmieneným návratom React zakazuje.
  const growRef = useAutoGrowTextarea(text);

  useEffect(() => {
    if (proposalId) {
      draft.clear();
      setError(null);
    }
  }, [proposalId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Poctivé už z konštrukcie: keď zadanie nie je, panel sa NEVYKRESLÍ. Stránka projektu potom vyzerá
  // presne tak, ako vyzerala doteraz — a väčšinu času Dedo nič nenavrhuje.
  if (!proposal) return null;

  const decidingAbout = proposal.id;
  const copy = ACTION_COPY[proposal.proposed_action] ?? FALLBACK_COPY;
  const edited = text.trim() !== proposal.content.trim();
  const canSend = busy === null && text.trim().length > 0;

  async function handleFailure(err: unknown, phrase: string): Promise<void> {
    if (err instanceof ApiError && err.status === 409) {
      setStaleNotice(err.message || "Zadanie sa medzitým zmenilo.");
      setError(null);
      try {
        onProposal(await getProjectDedoProposalApi(projectId));
      } catch {
        // Načítanie je zdvorilosť, nie záruka — nikdy nesmie prehltnúť vysvetlenie.
      }
      return;
    }
    setError(humanizeApiError(err, phrase));
  }

  async function send() {
    if (!canSend) return;
    setError(null);
    setStaleNotice(null);
    setBusy("send");
    try {
      const vysledok = await sendProjectDedoProposalApi(projectId, decidingAbout, text.trim());
      onProposal(null);
      await onVersion(vysledok.version_id, vysledok.started);
    } catch (err: unknown) {
      await handleFailure(err, "Spustenie zo zadania zlyhalo");
    } finally {
      setBusy(null);
    }
  }

  async function reject() {
    if (busy !== null) return;
    setError(null);
    setStaleNotice(null);
    setBusy("reject");
    try {
      await rejectProjectDedoProposalApi(projectId, decidingAbout);
      onProposal(null);
    } catch (err: unknown) {
      await handleFailure(err, "Zamietnutie zadania zlyhalo");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mb-6 rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] overflow-hidden">
      {/* OD KOHO to je — povedané prvé, lebo text nie je Manažérov a nikdy ho nesmie spustiť v domnení,
          že bol. Jantárová: niečo na zváženie, nie chyba a nie zelená. */}
      <div
        className={`flex items-center gap-2 ${WARNING_CHROME} px-4 py-2.5 text-sm font-semibold text-[var(--color-state-warning-fg)]`}
      >
        <Lightbulb className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <span>Dedo (náš technický tím) pripravil zadanie pre tento projekt</span>
      </div>

      <div className="flex flex-col gap-2 px-4 py-3">
        {staleNotice && (
          <p
            role="status"
            className="rounded-md border-l-2 border-[var(--color-state-warning-fg)] bg-[var(--color-state-warning-bg)] px-3 py-2 text-xs text-[var(--color-text-secondary)]"
          >
            {staleNotice}
          </p>
        )}

        <p className="text-xs text-[var(--color-text-muted)]">
          Text napísal Dedo — zatiaľ sa NIČ nezačalo a agent o ňom nevie. Môžeš ho upraviť alebo doplniť;
          spustiť ho môžeš len ty. {copy.effect}
        </p>

        <textarea
          lang="sk"
          spellCheck={true}
          ref={growRef}
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy !== null}
          aria-label="Zadanie od Deda"
          className="w-full resize-none rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-canvas)] px-3 py-1.5 text-xs text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:border-primary-500 focus:outline-none disabled:opacity-60"
        />

        {edited && (
          <p className="text-xs text-[var(--color-text-muted)]">
            Text si upravil — použije sa tvoje znenie. Pôvodný Dedov text zostáva zapísaný.
          </p>
        )}

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={reject}
            disabled={busy !== null}
            className="flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
            {busy === "reject" ? "Zamietam…" : "Zamietnuť zadanie"}
          </button>
          <button
            type="button"
            onClick={send}
            disabled={!canSend}
            className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Send className="h-3.5 w-3.5" aria-hidden="true" />
            {busy === "send" ? "Spúšťam…" : copy.button}
          </button>
        </div>

        <ErrorNote error={error} />
      </div>
    </div>
  );
}

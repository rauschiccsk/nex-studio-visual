// GettingStartedPage — a plain-Slovak "Ako začať" guide for a NON-EXPERT operator (handover).
//
// The self-sufficiency kernel: a non-expert (Tibor/Nazar) must be able to run the cockpit ALONE. This page
// walks them, in friendly plain Slovak with no dev-jargon, through the ACTUAL flow: prihlásenie → vytvoriť
// projekt → vytvoriť verziu a napísať Zadanie → čo znamenajú jednotlivé fázy (Príprava / Návrh / Programovanie
// / Overenie) → ako schvaľovať a odpovedať na otázky → nasadenie k zákazníkovi. Steps are derived from the real
// pages (NewProjectPage, NewVersionPage, RiadiaceCentrumPage, the phase labels + the Nasadenie/UAT flow).

import { useNavigate } from "react-router-dom";

interface Step {
  n: number;
  title: string;
  body: React.ReactNode;
}

const STEPS: Step[] = [
  {
    n: 1,
    title: "Prihlás sa",
    body: (
      <>
        Otvor NEX Studio a prihlás sa svojím <strong>menom a heslom</strong>. Ak heslo nemáš, vyžiadaj si ho od
        správcu. Po prihlásení uvidíš vľavo menu so všetkými časťami.
      </>
    ),
  },
  {
    n: 2,
    title: "Vytvor projekt",
    body: (
      <>
        V menu klikni na <strong>Projekty</strong> a potom na <strong>Nový projekt</strong>. Zadaj názov (napr.
        „NEX Ledger“), krátky identifikátor do adries (napr. <em>nex-ledger</em>) a vyber spôsob prihlásenia do
        aplikácie. Zvyšné polia môžeš nechať tak, ako sú. Klikni na <strong>Vytvoriť projekt</strong>.
      </>
    ),
  },
  {
    n: 3,
    title: "Vytvor verziu a napíš Zadanie",
    body: (
      <>
        V projekte klikni na <strong>Nová verzia</strong>. Do políčka <strong>Zadanie</strong> vlastnými slovami
        opíš, čo má aplikácia robiť — ciele, hlavné funkcie, pre koho to je. Nemusí byť dokonalé; AI Agent sa na
        nejasnosti sám doptá. Klikni <strong>Uložiť Zadanie</strong> a potom <strong>Spustiť tvorbu
        špecifikácie</strong>.
      </>
    ),
  },
  {
    n: 4,
    title: "Sleduj štyri fázy",
    body: (
      <>
        Prácu vidíš v <strong>Riadiacom centre</strong> ako rozhovor s AI Agentom. Postup má štyri fázy:
        <ul className="mt-2 space-y-1.5">
          <li>
            <strong>Príprava</strong> — AI Agent z tvojho Zadania spíše presnú špecifikáciu a doptá sa na
            nejasnosti.
          </li>
          <li>
            <strong>Návrh</strong> — pripraví návrh riešenia a rozpíše ho na plán úloh.
          </li>
          <li>
            <strong>Programovanie</strong> — naprogramuje aplikáciu podľa schváleného plánu.
          </li>
          <li>
            <strong>Overenie</strong> — nezávislý Audítor skontroluje, či hotová aplikácia naozaj robí to, čo
            bolo dohodnuté.
          </li>
        </ul>
      </>
    ),
  },
  {
    n: 5,
    title: "Schvaľuj a odpovedaj na otázky",
    body: (
      <>
        Na konci každej fázy sa AI Agent zastaví a počká na teba. Keď je všetko v poriadku, klikni na
        <strong> Schváliť</strong> a pokračuje ďalej. Keď sa niečo spýta, zobrazí sa otázka alebo tlačidlá na
        výber — stačí odpovedať do políčka alebo kliknúť na možnosť. Nič sa nedeje bez tvojho súhlasu.
      </>
    ),
  },
  {
    n: 6,
    title: "Nasaď aplikáciu k zákazníkovi",
    body: (
      <>
        Keď je verzia <strong>Hotová</strong>, klikni na <strong>Prejsť na nasadenie</strong> (alebo v menu na
        <strong> UAT</strong>). Najprv sa aplikácia nasadí do <strong>testovacieho prostredia (UAT)</strong>, kde
        si ju vyskúšaš. Keď je všetko v poriadku, nasadíš ju do <strong>ostrej prevádzky (PROD)</strong> pre
        zákazníka.
      </>
    ),
  },
];

export default function GettingStartedPage() {
  const navigate = useNavigate();

  return (
    <div className="mx-auto max-w-3xl p-6">
      <header className="mb-6">
        <h1 className="text-xl font-bold text-[var(--color-text-primary)]">Ako začať</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Krátky sprievodca pre úplný začiatok. Vedie ťa krok za krokom od prihlásenia až po nasadenie aplikácie
          zákazníkovi. Nepotrebuješ žiadne technické znalosti — všetko podstatné robí AI Agent, ty rozhoduješ a
          schvaľuješ.
        </p>
      </header>

      <ol className="space-y-3">
        {STEPS.map((step) => (
          <li
            key={step.n}
            className="flex gap-4 rounded-xl border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-4"
          >
            <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-primary-600 text-sm font-bold text-white">
              {step.n}
            </div>
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">{step.title}</h2>
              <div className="mt-1 text-sm leading-relaxed text-[var(--color-text-secondary)]">{step.body}</div>
            </div>
          </li>
        ))}
      </ol>

      <div className="mt-6 rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface-hover)] p-4">
        <p className="text-sm text-[var(--color-text-secondary)]">
          <strong className="text-[var(--color-text-primary)]">Tip:</strong> ak sa niekde zastavíš, hľadaj
          tlačidlo, ktoré ti povie, čo sa stane po kliknutí. Keď niečo zlyhá, zobrazí sa vysvetlenie v
          jednoduchom jazyku — nie je to tvoja chyba a nič nepokazíš.
        </p>
      </div>

      <div className="mt-6 flex justify-center">
        <button
          type="button"
          onClick={() => navigate("/projects")}
          className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-500"
        >
          Poďme na to — otvoriť Projekty
        </button>
      </div>
    </div>
  );
}

import type { AppKitStatus } from "@/services/api/projects";
import { LibraryUpgradePrompt } from "./LibraryUpgradePrompt";

const WORDS = {
  badge: "nex-app-kit",
  title: "K dispozícii je novšia šablóna aplikácie",
  subtitle: "Rám tejto aplikácie — ľavý panel, prihlásenie, Nastavenia a server — je pozadu za šablónou.",
  partsNote: "Povýšenie zmení oba naraz.",
  previewNote: "Po povýšení uvidíš nový rám hneď v náhľade Vizuál — to je zároveň tvoja vizuálna kontrola.",
};

/**
 * DEV-253: the opt-in upgrade of nex-app-kit in a project on an app template — shown when the Manažér founds a new
 * version and the project is behind the newest release. The frame and the server move together, in one commit.
 */
export function AppKitUpgradePrompt({
  status,
  busy = false,
  onUpgrade,
  onStay,
}: {
  status: AppKitStatus;
  busy?: boolean;
  onUpgrade: (target: string) => void;
  onStay: () => void;
}) {
  const offer = {
    current: status.current ?? null,
    latest: status.latest ?? null,
    behind: status.behind ?? 0,
    changelog: status.changelog ?? [],
    parts: (status.parts ?? []).map((p) => ({ key: p.name, label: p.label, current: p.current ?? null })),
  };
  return <LibraryUpgradePrompt offer={offer} words={WORDS} busy={busy} onUpgrade={onUpgrade} onStay={onStay} />;
}

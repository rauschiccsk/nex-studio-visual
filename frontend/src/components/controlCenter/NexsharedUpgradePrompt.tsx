import type { NexsharedStatus } from "@/services/api/projects";
import { LibraryUpgradePrompt } from "./LibraryUpgradePrompt";

const WORDS = {
  badge: "nex-shared",
  title: "K dispozícii je novší nex-shared",
  subtitle: "Táto aplikácia je pozadu za spoločným dizajnovým kitom.",
  partsNote: "Povýšenie zmení všetky naraz.",
  previewNote: "Po povýšení uvidíš nový vzhľad hneď v náhľade Vizuál — to je zároveň tvoja vizuálna kontrola.",
};

/**
 * Auto-notify nex-shared upgrade prompt (#3). Shown when the Manažér founds a new version and the app is behind the
 * latest published nex-shared. Upgrading rewrites the pin of every frontend (DEV-161), so the new version's Vizuál
 * preview and build run on the chosen nex-shared.
 */
export function NexsharedUpgradePrompt({
  status,
  busy = false,
  onUpgrade,
  onStay,
}: {
  status: NexsharedStatus;
  busy?: boolean;
  onUpgrade: (target: string) => void;
  onStay: () => void;
}) {
  const offer = { ...status, parts: status.frontends.map((f) => ({ key: f.directory, label: f.label, current: f.current })) };
  return <LibraryUpgradePrompt offer={offer} words={WORDS} busy={busy} onUpgrade={onUpgrade} onStay={onStay} />;
}

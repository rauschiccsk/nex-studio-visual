// Mená modelov na obrazovke (ICCINT-167).
//
// Kokpit nikde nemá zapísanú verziu modelu: ukladá a spúšťa RODINU (`opus`, `sonnet`, `haiku`) a Claude
// Code spustí jej najnovšiu verziu. Ktorá verzia to naozaj bola, hlási backend zo záznamu behov ako úplné
// meno (`claude-<rodina>-<verzia>`); tu sa z neho len poskladá meno pre človeka — bez zoznamu známych verzií,
// takže nová verzia sa ukáže správne bez zmeny aplikácie. Príklady sú v skúške `test_modelLabel`.

import type { AgentModelOption } from "@/types/user_agent_setting";

function capitalize(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

/** `opus` → „Opus". */
export function familyLabel(family: string): string {
  return capitalize(family);
}

/**
 * Úplné meno z CLI → meno pre človeka: rodina s veľkým písmenom a čísla verzie spojené bodkou; dátum
 * vydania (osem číslic) sa vynechá; holá rodina ostane rodinou. Meno, v ktorom niet slova rodiny, sa vráti
 * nezmenené — radšej surové než vymyslené.
 */
export function modelDisplayName(fullId: string): string {
  const tokens = fullId.replace(/^claude-/, "").split("-");
  const family = tokens.find((t) => !/^\d+$/.test(t));
  if (!family) return fullId;
  const version = tokens.filter((t) => /^\d{1,7}$/.test(t)).join(".");
  return version ? `${familyLabel(family)} ${version}` : familyLabel(family);
}

/** Voľba v Nastaveniach: „Opus — vždy najnovší (naposledy bežal Opus 5.5)". */
export function modelOptionLabel(option: AgentModelOption): string {
  const base = `${familyLabel(option.id)} — vždy najnovší`;
  return option.last_run_model ? `${base} (naposledy bežal ${modelDisplayName(option.last_run_model)})` : base;
}

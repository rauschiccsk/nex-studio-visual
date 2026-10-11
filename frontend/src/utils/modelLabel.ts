// Model names on screen (ICCINT-167).
//
// The cockpit nowhere stores a model version: it stores and launches a FAMILY (`opus`, `sonnet`, `haiku`) and Claude
// Code launches its latest version. Which version it really was is reported by the backend from the run log as a full
// name (`claude-<family>-<version>`); here only a human-readable name is built from it — no list of known versions,
// so a new version shows correctly without changing the application. Examples are in the test `test_modelLabel`.

import type { AgentModelOption } from "@/types/user_agent_setting";

function capitalize(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

/** `opus` → „Opus". */
export function familyLabel(family: string): string {
  return capitalize(family);
}

/**
 * Full name from the CLI → human name: capitalised family and version numbers joined by a dot; the release
 * date (eight digits) is omitted; a bare family stays a family. A name with no family word is returned
 * unchanged — better raw than invented.
 */
export function modelDisplayName(fullId: string): string {
  const tokens = fullId.replace(/^claude-/, "").split("-");
  const family = tokens.find((t) => !/^\d+$/.test(t));
  if (!family) return fullId;
  const version = tokens.filter((t) => /^\d{1,7}$/.test(t)).join(".");
  return version ? `${familyLabel(family)} ${version}` : familyLabel(family);
}

/** Choice in Settings: „Opus — vždy najnovší (naposledy bežal Opus 5.5)". */
export function modelOptionLabel(option: AgentModelOption): string {
  const base = `${familyLabel(option.id)} — vždy najnovší`;
  return option.last_run_model ? `${base} (naposledy bežal ${modelDisplayName(option.last_run_model)})` : base;
}

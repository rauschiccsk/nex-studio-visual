// DEV-395: the languages an app on the template speaks, as the cockpit keeps them — the main one first.

export interface LanguageOption {
  code: string;
  label: string;
}

/** The list the backend keeps: the main language first, the others in the order the cockpit offers them. */
export function withMain(options: LanguageOption[], main: string, others: string[]): string[] {
  return [main, ...options.map((o) => o.code).filter((code) => code !== main && others.includes(code))];
}

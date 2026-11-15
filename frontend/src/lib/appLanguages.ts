// DEV-395: the languages an app on the template speaks, as the cockpit keeps them — the main one first.

export interface LanguageOption {
  code: string;
  label: string;
}

/** The list the backend keeps: the main language first, the others in the order the cockpit offers them. */
export function withMain(options: LanguageOption[], main: string, others: string[]): string[] {
  return [main, ...options.map((o) => o.code).filter((code) => code !== main && others.includes(code))];
}

/** DEV-399: the chosen languages the project's nex-app-kit does not speak yet. */
export function unknownToKit(languages: string[], known: string[]): string[] {
  return languages.filter((code) => !known.includes(code));
}

/** DEV-399: the oldest nex-app-kit release that speaks every one of `languages` (`since`: the release of each). */
export function kitNeeded(languages: string[], since: Record<string, string>): string {
  const parts = (v: string) => v.split(".").map(Number);
  const newer = (a: string, b: string) => {
    const [x, y] = [parts(a), parts(b)];
    for (let i = 0; i < 3; i++) if ((x[i] ?? 0) !== (y[i] ?? 0)) return (x[i] ?? 0) > (y[i] ?? 0);
    return false;
  };
  return languages.map((code) => since[code]).filter((v): v is string => Boolean(v)).reduce((a, b) => (newer(b, a) ? b : a), "");
}

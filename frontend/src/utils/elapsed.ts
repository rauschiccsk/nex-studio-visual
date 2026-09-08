/**
 * Ako dlho už niečo beží — ľudsky, nie na sekundu (ICCINT-75).
 *
 * Manažér nepotrebuje presný čas; potrebuje rozoznať ťah spustený pred chvíľou od ťahu, ktorý visí
 * tretiu hodinu. Bez toho vyzerá ticho pri práci rovnako ako ticho pri poruche a on nemá ako
 * rozhodnúť, či počkať, kliknúť znova, alebo volať pomoc.
 *
 * Býva to vo vlastnom súbore, nie pri pruhu stavu: modul, ktorý vyváža komponent aj funkciu, rozbíja
 * rýchle obnovovanie počas vývoja (a `eslint` naň upozorňuje).
 */
export function elapsedSince(since: string | null, now: number): string {
  if (!since) return "";
  const started = new Date(since).getTime();
  if (Number.isNaN(started)) return "";
  const seconds = Math.max(0, Math.round((now - started) / 1000));
  if (seconds < 60) return "pracuje sa pár sekúnd";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `pracuje sa ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const zvysok = minutes % 60;
  return zvysok ? `pracuje sa ${hours} h ${zvysok} min` : `pracuje sa ${hours} h`;
}

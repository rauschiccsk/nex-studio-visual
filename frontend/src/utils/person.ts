/**
 * Ako sa človek volá — meno a priezvisko, inak prihlasovacie meno (ICCINT-81).
 *
 * Manažér pracuje s ľuďmi, nie s účtami: v ponuke „komu zveriť projekt“ ani v paneli vedľa nej nemá
 * čítať `tibi` a v hlave si to prekladať na Tibora. Director to zadal 08.09.2026 hneď po prvom
 * zverení projektu.
 *
 * Prečo pomocník, a nie štvrtá kópia toho výrazu: tá istá skladačka
 * `[first_name, last_name].filter(Boolean).join(" ") || username` bola rozpísaná na TROCH miestach
 * (Sidebar, NewProjectPage 2×). Štvrtá kópia je presne to miesto, kde sa raz jedna z nich zmení
 * a ostatné nie — a používateľ potom vidí toho istého človeka inde inak.
 *
 * Prihlasovacie meno je záložná možnosť, nie chyba: konto `admin` priezvisko vyplnené nemá.
 * Prázdny riadok by bol horší než `admin`.
 *
 * Keď človek nie je (odhlásený, neznáme id), vracia sa prázdny reťazec — zástupný znak si dosadí
 * volajúci. Pomocník na formátovanie mena nemá rozhodovať, ako vyzerá prázdne miesto v cudzom paneli.
 */
export function personName(
  person: { first_name?: string | null; last_name?: string | null; username: string } | null | undefined,
): string {
  if (!person) return "";
  const full = [person.first_name, person.last_name]
    .map((part) => (part ?? "").trim())
    .filter(Boolean)
    .join(" ");
  return full || person.username;
}

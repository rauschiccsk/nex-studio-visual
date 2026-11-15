// DEV-340 — the UAT guide is one for every customer, so it writes a placeholder where an address goes
// (`https://<adresa-inštalácie>/api/v1/health`); the UAT tab puts the full address of the customer being tried in its
// place, as a link. The placeholders and each customer's addresses come from the server (uat_guide.PLACEHOLDERS).
import type { UatGuide } from "@/types/deploy";

export type GuidePart = { text: string } | { url: string };

/** What each placeholder stands for at one customer's UAT — nothing for a customer without a UAT installation. */
export function addressesOf(guide: UatGuide | undefined, customerId: string, hasUat: boolean): Record<string, string> {
  if (!hasUat) return {};
  return Object.fromEntries(
    (guide?.addresses ?? []).filter((a) => a.customer_id === customerId).map((a) => [a.placeholder, a.url]),
  );
}

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Where a path ends: before what closes the sentence or the code mark, and before the sentence's own full stop. */
const PATH = "([^\\s`'\"“”„)\\]]*?)(?=[.,;:!?]*(?:[\\s`'\"“”„)\\]]|$))";

/** A sentence of the guide with every placeholder (its scheme, path and code marks along) as the full address. */
export function withAddresses(text: string, addresses: Record<string, string>): GuidePart[] {
  const placeholders = Object.keys(addresses);
  if (placeholders.length === 0) return [{ text }];
  const pattern = new RegExp(`\`?(?:https?://)?(${placeholders.map(escape).join("|")})${PATH}\`?`, "g");
  const parts: GuidePart[] = [];
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const [whole, placeholder = "", path = ""] = match;
    if (match.index > last) parts.push({ text: text.slice(last, match.index) });
    parts.push({ url: (addresses[placeholder] ?? placeholder) + path });
    last = match.index + whole.length;
  }
  if (last < text.length) parts.push({ text: text.slice(last) });
  return parts;
}

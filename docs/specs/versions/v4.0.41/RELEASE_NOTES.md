# v4.0.41 — NEX Studio Visual

## AI Agent beží priamo na serveri

Spustenie tvorby špecifikácie (a všetkých ďalších fáz) padalo, lebo serverové prostredie nemalo samotný **AI nástroj (claude)** ani **Node.js** — takže backend nevedel spustiť agenta. Doplnené:

- **AI Agent (claude)** — do backendu je pripojený presne ten istý, overený agent, ktorý používa tím, takže sa správa rovnako.
- **Node.js** — potrebné, keď agent stavia časti aplikácie.

Týmto sa backend stáva **samostatným motorom**: používateľ spustí tvorbu špecifikácie → agent ju vytvorí → a pokračuje sa v stavbe a nasadení — všetko priamo z cockpitu, bez ručných zásahov na serveri.

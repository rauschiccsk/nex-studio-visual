# E1 Phase A — NEX Studio stack upgrade + design-token foundation (the vzor)

> **E1 (cross-project unification), Phase A.** Director decisions (2026-06-12): mechanism = shared library;
> start with **NEX Studio as the vzor** (it becomes the canonical design source); auth = two modes (login /
> Genesis token — Phase C). Phase A brings NEX Studio UP to the modern stack the projects it builds already
> run (React 19 + Tailwind v4) so it can later share components, and establishes its design tokens in the
> Tailwind-v4 `@theme` form that Phase B extracts into the shared library.
> Grounded 2026-06-12 (4-lens discovery). Reference for the target stack = **nex-ledger** (cleanest modern
> setup: React 19.2 + Tailwind 4.3 + `@tailwindcss/vite`, no postcss/tailwind.config).

## Grounded reality (what makes this safe)
- **React 18→19 is LOW risk.** NEX Studio's code is clean for React 19: NO `forwardRef`, class components,
  string refs, `PropTypes`, `defaultProps`, `React.createElement`, or legacy Context; `useRef` (13 sites) all
  properly typed; `main.tsx` already uses `createRoot`. Deps are React-19 peer-compatible (recharts 2.15.4,
  lucide-react, zustand 5). → mostly a version bump.
- **Tailwind v3→v4 is the real work (HIGH risk).** Config moves from JS (`tailwind.config.js` +
  `postcss.config.js` + `@tailwind` directives) to CSS (`@import "tailwindcss"` + `@theme` + `@custom-variant`)
  via the `@tailwindcss/vite` plugin. Default styles + some utilities changed in v4 → the whole UI must be
  visually re-verified.
- **React Router:** Studio on v6.28, the v6 APIs it uses (`BrowserRouter/Routes/Route/Navigate/useLocation/
  useNavigate`) are v7-compatible, but **nex-inbox is also still on v6** → **RR v7 is DEFERRED to Phase B**
  (out of scope here; keep v6 to minimize Phase A surface).
- **Tests:** 36/220 FE tests already fail pre-migration (incomplete `react-router-dom` mock missing
  `useLocation`; dark-mode jsdom class-pollution; the E4 Slovak string-assertion failures). Phase A touches
  exactly these areas → the long-deferred FE-vitest cleanup is folded in here (A4).

## Scope

### A1 — React 18 → 19 (low risk)
- `package.json`: `react`/`react-dom` → `^19` (match nex-ledger 19.2.x); `typescript` → `^5.7`; bump
  `@types/react`/`@types/react-dom` to v19.
- `tsconfig.app.json`: `target` ES2020 → **ES2022** (align modern stack).
- `main.tsx`: optionally switch to the named `import { createRoot } from "react-dom/client"` +
  `import { StrictMode } from "react"` form (cosmetic; current pattern is already valid). No app-code changes
  expected — but run `tsc` and fix any v19 type tightening.
- Verify recharts (MetricsPage) renders under React 19; bump only if it breaks.

### A2 — Tailwind v3 → v4 (high risk — the core)
Mirror the **nex-ledger** recipe:
- `package.json`: remove `tailwindcss@3`, `autoprefixer`, `postcss` (if only used for Tailwind); add
  `tailwindcss@^4.3` + `@tailwindcss/vite@^4.3`.
- **Delete** `tailwind.config.js` and `postcss.config.js`.
- `vite.config.ts`: add the plugin → `plugins: [react(), tailwindcss(), viteStaticCopy({...})]`. **PRESERVE the
  `viteStaticCopy` block** that bundles the Slovak hunspell dictionary (`dictionary-sk` → `/dictionaries/sk/`)
  — domain-specific, must stay. Keep the dev server `port: 9177` + `/api → localhost:9176` proxy.
- `src/index.css`: rewrite from `@tailwind base/components/utilities` to:
  - `@import "tailwindcss";`
  - `@custom-variant dark (&:where(.dark, .dark *));` (replaces `darkMode: 'class'`)
  - the `@theme { … }` token block (see A3),
  - the custom component classes (`.btn`, `.btn-primary`, `.btn-secondary`, `.card`) preserved via a
    v4-compatible `@layer components { … @apply … }` block (keep identical visual result).
- `@tailwindcss/typography`: removed as a v3 plugin; if any `prose` usage exists, re-add via v4
  `@plugin "@tailwindcss/typography";` in the CSS, else drop it.
- **Dark mode identity preserved:** NEX Studio is dark-by-default (`<html class="dark">`) with the per-user
  `ThemeContext` (`nex_dark_{username}` localStorage). KEEP both — only the Tailwind dark *mechanism* changes
  (`@custom-variant`). Do NOT switch to a Zustand uiStore; the existing ThemeContext stays.
- **Visual re-verification is mandatory** — v4 changed default border color, ring, placeholder, etc. Sweep the
  main surfaces (Sidebar, Topbar, Cockpit, Settings, Metrics charts, forms, tables) light + dark.

### A3 — Canonical design tokens (the vzor foundation)
NEX Studio is the vzor → its palette becomes the canonical token set, expressed as a Tailwind-v4 `@theme`
block (the design source Phase B extracts into the shared library):
- Port the EXACT current palette from `tailwind.config.js` into `@theme` as CSS variables: the **primary
  indigo** scale (50–950, DEFAULT `#6366f1`), the **status** colors (planned/in-design/in-development/done/
  failed), fonts (Inter / JetBrains Mono). Keep the values identical — this is a representation change, not a
  re-brand.
- Add the dark-scheme values as the `.dark` overrides (Studio is dark-first, so the dark values are primary).
- Token DEPTH: **comprehensive semantic set** (surface/text/border/accent/status families) modelled on
  nex-inbox's `@theme`, but with **Studio's indigo + dark-first values** as canonical (NOT inbox's blue/
  light-first). This is the deliberate vzor: Studio's look wins, others align to it in Phase D.
- Document the token names in a short comment block — they are the contract Phase B's shared library inherits.

### A4 — Test infrastructure fix (folds the deferred FE-vitest cleanup)
- Complete the `react-router-dom` mock (add `useLocation`, `useNavigate`, etc. via `importOriginal`) so
  `test_LoginPage` and route tests stop failing on missing exports.
- Fix dark-mode test isolation (reset `document.documentElement.classList` between tests — the
  "expected true to be false" pollution).
- Update the E4 Slovak string-assertion failures (tests asserting old English labels → the Slovak the UI now
  renders) — using precise matchers (avoid the `/meno/i` ambiguity between "Meno" and "Používateľské meno").
- Target: the FE vitest suite is **green** (0 failures) after Phase A — this finally clears the 25 + 11
  deferred failures. (Keep vitest/jsdom at Studio's current newer versions — 4.x/29 — they work with React 19;
  do not downgrade to match nex-inbox.)

## Seams to preserve (do NOT change in Phase A)
The Slovak UI (E4) stays — only re-verify labels survive; `viteStaticCopy` Slovak dictionary; dark-by-default
+ per-user `ThemeContext`; dev port 9177 + `/api` proxy; recharts MetricsPage; ALL existing routes/behavior;
the backend (untouched — FE-only CR). **No shared library yet** (that is Phase B) — Phase A only modernizes
NEX Studio in place + lays the token foundation.

## Out of scope (deferred)
- React Router v6 → v7 (Phase B, with the shared library).
- React Query / react-hook-form / zod / sonner adoption (feature libraries, not the stack migration).
- Creating the shared library + extracting components (Phase B).
- Migrating Inbox/Ledger (Phase D).

## Acceptance
- `npm run build` (tsc + vite) clean; `npm run lint` 0 errors.
- FE vitest suite **green** (the A4 fixes land the previously-failing files).
- Tailwind v4 active: no `tailwind.config.js` / `postcss.config.js`; `@tailwindcss/vite` in `vite.config.ts`;
  `index.css` uses `@import "tailwindcss"` + `@theme` + `@custom-variant`.
- React 19 + react-dom 19 installed; app boots; no console errors.
- **Visual parity, light + dark:** Sidebar, Topbar, Cockpit board, Settings (all tabs), Metrics charts,
  Projects/Versions, forms, tables render the same as before (dark-by-default preserved). Slovak labels intact.
- The `@theme` token block carries Studio's canonical palette (indigo + dark-first), documented as the
  Phase-B contract.
- CI green (Lint, Test, Build Frontend, Build Docker, Deploy) — the live cockpit redeploys cleanly.

## Risk + mitigation
Tailwind v4's changed defaults are the main risk on a LIVE cockpit. Mitigation: migrate config + CSS, then a
mandatory visual sweep (light+dark) of every main surface before push; adversarial pre-push verification
(Dedo) on the diff; CI deploy + a Director smoke-look after deploy. If a v4 default visibly changes a surface,
pin it back explicitly in `@theme`/`@layer base` rather than leaving it drifted.

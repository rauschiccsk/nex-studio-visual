/**
 * ESLint flat config for the NEX Studio Visual frontend.
 *
 * GATE STATUS: enforced. `npm run lint` runs in CI (`.github/workflows/ci.yml`, job
 * "Build Frontend", step "ESLint") and in `.githooks/pre-commit` — the same command in all three
 * places, so what passes locally passes in CI. Until 2026-07-28 this config existed but no gate
 * ever executed it, while the cockpit forced `npm run lint` on every project it generated.
 *
 * WARNINGS POLICY — errors fail, warnings are ratcheted:
 *   - Any error fails the gate (eslint exits 1). Not negotiable.
 *   - Warnings are capped at the baseline by `--max-warnings 5` in the `lint` npm script, so a new
 *     warning turns CI red instead of quietly joining the pile. The baseline was NOT set to 0
 *     because the 5 warnings below live in `src/**` components owned elsewhere; capping first
 *     stops the growth today, fixing them lowers the cap tomorrow.
 *   - The cap is a RATCHET: it may only ever go DOWN. Raising it to make a red build green is a
 *     threshold downgrade — fix the warning instead.
 *
 * Baseline (5 warnings, 2026-07-28; 3 since DEV-155, 22.10.2026) — lower `--max-warnings` as these are fixed:
 *   1. src/contexts/ThemeContext.tsx:40             react-refresh/only-export-components
 *   2. src/contexts/ThemeContext.tsx:137            react-refresh/only-export-components
 *   3. src/pages/KnowledgeBasePage.tsx:382          unused eslint-disable directive
 */
import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "test-results", "playwright-report"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
    },
  },
);

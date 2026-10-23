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
 *   - Warnings are capped by `--max-warnings` in the `lint` npm script, so a new warning turns CI red
 *     instead of quietly joining the pile. The cap started at the 5 warnings of 2026-07-28 and was
 *     lowered as they were fixed; it is 0 now.
 *   - The cap is a RATCHET: it may only ever go DOWN. Raising it to make a red build green is a
 *     threshold downgrade — fix the warning instead.
 *
 * Baseline (5 warnings, 2026-07-28; 3 since DEV-155, 22.10.2026; 0 since DEV-199, 23.10.2026) — the cap is 0: a
 * warning fails the gate like an error.
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

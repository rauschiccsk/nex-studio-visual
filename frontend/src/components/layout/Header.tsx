/**
 * Application header — displays project title, active module badge, and user menu.
 *
 * Per DESIGN.md § 3.2, the Header is a stateful top bar that will be wired to
 * projectStore / moduleStore / authStore in later tasks. For now it is a layout
 * placeholder so that the overall chrome is in place.
 *
 * Dark-mode toggle (Moon/Sun icon) is always visible per DESIGN.md § 3.3a.
 */
import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/contexts/ThemeContext";

function Header() {
  const { isDark, toggleDark } = useTheme();

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-gray-200 bg-white px-6 dark:border-gray-700 dark:bg-gray-900">
      <div className="flex items-center gap-3">
        <h1 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          NEX Studio
        </h1>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          aria-label={isDark ? "Prepnúť na svetlý režim" : "Prepnúť na tmavý režim"}
          onClick={toggleDark}
          className="rounded-full p-2 text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
        >
          {isDark ? <Sun size={18} /> : <Moon size={18} />}
        </button>
        <button
          type="button"
          className="rounded-full bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
        >
          Účet
        </button>
      </div>
    </header>
  );
}

export default Header;

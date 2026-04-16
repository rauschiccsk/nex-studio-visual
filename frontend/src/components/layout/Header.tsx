/**
 * Application header — displays project title, active module badge, and user menu.
 *
 * Per DESIGN.md § 3.2, the Header is a stateful top bar that will be wired to
 * projectStore / moduleStore / authStore in later tasks. For now it is a layout
 * placeholder so that the overall chrome is in place.
 */
function Header() {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-gray-200 bg-white px-6">
      <div className="flex items-center gap-3">
        <h1 className="text-base font-semibold text-gray-900">NEX Studio</h1>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          className="rounded-full bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-200"
        >
          Account
        </button>
      </div>
    </header>
  );
}

export default Header;

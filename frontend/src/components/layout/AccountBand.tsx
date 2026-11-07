import { useEffect } from "react";
import { useAuthStore } from "@/store/authStore";
import { personName } from "@/utils/person";

/** DEV-320: whose window this is — the account's colour in a thin band across the top, its name in the window's
 *  title (the taskbar). Several accounts run side by side, each in its own Chrome profile, and an app window does not
 *  show the profile's colour, so the cockpit does. */
export default function AccountBand() {
  const user = useAuthStore((s) => s.user);
  const name = user ? personName(user) || user.username : "";

  useEffect(() => {
    document.title = name ? `${name} · NEX Studio Visual` : "NEX Studio Visual";
  }, [name]);

  if (!user?.accent_color) return null;
  return (
    <div
      aria-hidden
      data-testid="account-band"
      className="pointer-events-none fixed inset-x-0 top-0 z-[60] h-[3px]"
      style={{ backgroundColor: user.accent_color }}
    />
  );
}

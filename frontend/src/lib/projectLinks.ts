// Links INTO the project page that land on a specific step (DEV-57) — one definition, read by the page that
// honours them and by every screen that sends the manager there.

/** `/projects/<slug>?rychla-oprava=1` opens the „Rýchla oprava“ dialog on arrival. */
export const FAST_FIX_PARAM = "rychla-oprava";

/** `/projects/<slug>#zadanie-od-deda` scrolls to the brief Dedo prepared for the project. */
export const DEDO_BRIEF_ANCHOR = "zadanie-od-deda";

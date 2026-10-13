import { useLocation, useNavigate } from "react-router-dom";
import { Sidebar as ShellSidebar, NavItem, SectionDivider, Brand, UserCard, NavIcon, useShell } from "nex-shared";
import { useAuthStore } from "@/store/authStore";
import { useActiveContextStore } from "@/store/activeContextStore";
import { usePresenceStore } from "@/store/usePresenceStore";
import { useUserLayoutStore } from "@/store/userLayoutStore";
import { usePipelineWs } from "@/hooks/usePipelineWs";
import { useBackendVersion } from "@/hooks/useBackendVersion";
import { useCiMain } from "@/hooks/useCiMain";
import { verziaDoPanela } from "@/lib/verziaDoPanela";
import type { UserRole } from "@/types/user";
import { personName } from "@/utils/person";

// UserCard subtitle (CR-NS-093): derive the "<Title> · <Code>" label from the
// logged-in user's role. Studio provisions all three roles (ri/ha/shu) — titles
// match SettingsPage ROLE_OPTIONS (ri → Manažér, ha → Medior, shu → Junior).
// CR-V2-004: operator label Director → Manažér (the ``ri`` ACCESS-ROLE token is
// unchanged — auth gate stays per design §8; this is a display relabel only).
const ROLE_LABEL: Record<UserRole, string> = {
  ri: "Manažér",
  ha: "Medior",
  shu: "Junior",
};

// ─── Icon helpers ───────────────────────────────────────────────────────────
// Director directive 2026-05-15: full Unicode emoji glyphs instead of
// monochrome SVG line-art. Browser renders these via the system emoji
// font (Segoe UI Emoji on Windows, Apple Color Emoji on macOS, Noto
// Color Emoji on Linux/ANDROS) so they appear fully colored without
// any CSS class. ``aria-hidden`` because the NavItem text label is the
// accessible name; emoji is decorative.

// Colored nav glyphs via the shared <NavIcon> (E1 chrome unification, CR-NS-067).
// FINAL v2.0.0 sidebar glyphs (CR-V2-019, design §4.1).
const IconHome = () => <NavIcon glyph="🏠" />;
const IconGuide = () => <NavIcon glyph="🧭" />;
const IconNotes = () => <NavIcon glyph="📝" />;
const IconFolder = () => <NavIcon glyph="📁" />;
const IconVersions = () => <NavIcon glyph="🌿" />;
const IconBacklog = () => <NavIcon glyph="📋" />;
const IconMetrics = () => <NavIcon glyph="📊" />;

// v2 spine STEP 1 (Chrbtica): the Riadiace centrum = the ONE conversation-centred
// build surface (replaces the AI Agent terminal + the 4-phase Vývoj board);
// Špecifikácia = the read-only spec shell.
const IconRiadiace = () => <NavIcon glyph="🎛️" />;
const IconPoradca = () => <NavIcon glyph="💬" />;
const IconSpec = () => <NavIcon glyph="📄" />;
// DEV-84: the CI runs of the selected project's repository.
const IconRepozitar = () => <NavIcon glyph="🔀" />;
// DEV-86: the selected project's files as VS Code shows them, read-only.
const IconNsCode = () => <NavIcon glyph="💻" />;

// CR-1 (nex-studio-visual): the Vizuál surface — the live app preview (monitor 2) during the vizual phase.
const IconVizual = () => <NavIcon glyph="🖥️" />;

// v2 per-customer deploy surfaces (pages land in Milestone G — CR-V2-025/027).
const IconCustomers = () => <NavIcon glyph="👥" />;
const IconUat = () => <NavIcon glyph="🧪" />;
const IconProd = () => <NavIcon glyph="🚀" />;

const IconKbBook = () => <NavIcon glyph="📚" />;

const IconUpdates = () => <NavIcon glyph="✨" />;
const IconSettings = () => <NavIcon glyph="⚙️" />;
const IconKey = () => <NavIcon glyph="🔑" />;

// ─── Sidebar ─────────────────────────────────────────────────────────────────
// Thin composition over the shared <Sidebar> shell (E1 Phase B2, CR-NS-049):
// the generic frame / nav primitives live in nex-shared; ALL NEX-Studio-specific
// behavior stays here — routes, active detection (useLocation), the selected
// project/version indicator, project-scoped disabled items, the cockpit awaiting
// badge, the admin submenu, the E6 presence toggle, Credentials ri-gating, and
// the user footer. Collapse state is owned here and passed to the shell.

export default function Sidebar() {
  // DEV-82: the width and the collapsed state are the person's own, kept on the server (until they arrive, the
  // sidebar keeps its default width, expanded).
  const layout = useUserLayoutStore((s) => s.layout);
  const saveLayout = useUserLayoutStore((s) => s.save);
  const railCollapsed = layout?.sidebar_collapsed ?? false;
  const widthLimit = layout?.limits.sidebar_width;
  // DEV-62: on a narrow screen the sidebar is the shell's drawer and is never collapsed to icons.
  const { narrow } = useShell();
  const collapsed = railCollapsed && !narrow;
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const isAway = usePresenceStore((s) => s.isAway);
  const setIsAway = usePresenceStore((s) => s.setIsAway);
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const selectedVersion = useActiveContextStore((s) => s.selectedVersion);
  const setSelectedProject = useActiveContextStore((s) => s.setSelectedProject);

  // Sidebar-level pipeline WS on the pinned version (F-007 §7). Doubles as the
  // §9 Manažér-presence signal; drives the cockpit "awaiting" attention dot.
  // `accessDenied` = the pinned project's pipeline is owner-or-ri and this user is
  // neither (the project LIST is wider than the pipeline gate, so a Medior CAN pin
  // a project he may not drive) — the Riadiace centrum entry then says so instead
  // of opening a screen that can only show an empty board.
  const { board: pipelineBoard, accessDenied: pipelineAccessDenied } = usePipelineWs(
    selectedVersion?.versionId ?? null,
  );
  const cockpitAwaiting = pipelineBoard?.state?.status === "awaiting_manazer";

  const isActive = (path: string) =>
    path === "/" ? location.pathname === "/" : location.pathname.startsWith(path);

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  // CR-NS-089: show the logged-in user's full name (first + last), falling back
  // to username → email. Initials derive from the same resolved source.
  const displaySource = personName(user) || user?.email || "";
  const displayName = displaySource || "—";
  const initials = displaySource ? displaySource.slice(0, 1).toUpperCase() : "?";

  const verziaChrbtice = useBackendVersion();
  const hasProject = Boolean(selectedProject);
  // DEV-85 — the repository tab is red while a workflow's newest run on the project's main branch is red.
  const ciMain = useCiMain(hasProject && !pipelineAccessDenied ? selectedProject!.slug : null);
  const ciRed = (ciMain?.failing.length ?? 0) > 0;
  const projectsFallback = "/projects";

  // ─── Logo slot (shared Brand — E1 chrome unification, CR-NS-067) ───────────
  // ICCINT-128: číslo vedie BACKEND. `VITE_APP_VERSION` je verzia frontendu a panel ju dovtedy
  // vydával za verziu celej appky — 14.09.2026 o štyri vydania vedľa. Pri zhode sa nič nemení.
  const logo = (
    <Brand
      initials="NSV"
      name="NEX Studio Visual"
      version={verziaDoPanela(verziaChrbtice, import.meta.env.VITE_APP_VERSION)}
    />
  );

  // ─── Footer slot ─────────────────────────────────────────────────────────
  // E6 (CR-NS-038; broadened v4.0.48): presence toggle for EVERY operator, not just the admin. "Preč" →
  // agent-needs-operator events ping Telegram even with the cockpit open. Since the v4.0.35 ownership model a Junior
  // (shu) OWNS + operates their own projects, so they need the same presence signal — the backend notify is already
  // per-user. DEV-81: one line with the user card — the state is the icon and its color; the full sentence is in the
  // tooltip and the accessible name.
  const presence = user && (
    <button
      type="button"
      onClick={() => setIsAway(!isAway)}
      aria-pressed={isAway}
      aria-label={isAway ? "Preč — prepnúť na Pri počítači" : "Pri počítači — prepnúť na Preč"}
      title={
        isAway
          ? "Preč — upozornenia na Telegram zapnuté aj s otvoreným Riadiacim centrom. Klikni pre „Pri počítači“."
          : "Pri počítači — bez Telegram upozornení (vidíš Riadiace centrum). Klikni pred odchodom od počítača."
      }
      className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-sm leading-none transition-colors ${
        isAway
          ? "bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]"
          : "hover:bg-[var(--color-surface-hover)]"
      }`}
    >
      {isAway ? "🌙" : "🟢"}
    </button>
  );
  // v4.0.50: "Moje konto" moved OUT of the sidebar INTO Settings (Nastavenia → Moje konto tab, next to the admin
  // "Používatelia" tab) — account management lives systematically in Settings.
  const role = user?.role ? ROLE_LABEL[user.role] : "—";
  const footer = (
    <UserCard
      initials={initials}
      name={displayName}
      subtitle={role}
      onLogout={handleLogout}
      actions={presence}
    />
  );

  return (
    // FINAL v2.0.0 navigation (CR-V2-019, design §4.1). Order top-to-bottom:
    //   Prehľad · Projekty[📌 pin] · Verzie · Zásobník · AI Agent · Vývoj ·
    //   Zákazníci · UAT · PROD · Metriky · Dokumentácia · Prístupy ·
    //   Aktualizácie · Nastavenia.  Footer: presence 🟢/🌙 + user card.
    //
    // AUD-7 — INVARIANT (do NOT violate): there is intentionally NO Auditor /
    // Audítor nav item. The Auditor's verdict and findings are reachable ONLY
    // via Vývoj → Verifikácia (CR-V2-021). A future edit must never add an
    // Auditor entry here — the verifier is independent and surfaces inside the
    // build board, not as a standalone destination (design §4.1, §2.4).
    <ShellSidebar
      collapsed={railCollapsed}
      onToggleCollapse={() => void saveLayout({ sidebar_collapsed: !railCollapsed })}
      logo={logo}
      footer={footer}
      // DEV-81: 28 px entries, a two-line brand and a one-line footer — room for the tabs still to come.
      dense
      width={layout?.sidebar_width ?? undefined}
      // The default width is kept as „none chosen“ — it then follows the default if that ever changes.
      onWidthChange={
        widthLimit ? (w) => void saveLayout({ sidebar_width: w === widthLimit.default ? null : w }) : undefined
      }
      minWidth={widthLimit?.min}
      maxWidth={widthLimit?.max}
      defaultWidth={widthLimit?.default}
    >
      <NavItem icon={<IconHome />} label="Prehľad" active={isActive("/")} onClick={() => navigate("/")} />
      {/* Getting-started guide for a non-expert operator (handover). Always accessible (not project-scoped). */}
      <NavItem icon={<IconGuide />} label="Ako začať" active={isActive("/getting-started")} onClick={() => navigate("/getting-started")} />
      {/* DEV-89: the person's own quick notes — not tied to the selected project, so among the general entries. */}
      <NavItem icon={<IconNotes />} label="Poznámky" active={isActive("/notes")} onClick={() => navigate("/notes")} />
      <NavItem icon={<IconFolder />} label="Projekty" active={isActive("/projects")} onClick={() => navigate("/projects")} />

      {/* Selected project indicator — placed directly under Projects
          (Director directive 2026-05-15: belongs near the source of
          the Pin action). Pin icon → user explicitly chose this
          project in /projects. Version suffix appears once the user
          opens a verzia (auto-set by useActiveContextSync). */}
      {hasProject && !collapsed && (
        <div className="px-3 pb-1 flex items-center gap-1.5 text-[10px] text-[var(--color-text-muted)] font-mono">
          <svg className="w-3 h-3 shrink-0 text-primary-400" fill="currentColor" viewBox="0 0 24 24">
            <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z" />
          </svg>
          <span className="truncate flex-1">
            {selectedProject!.name}
            {selectedVersion && (
              <span className="text-[var(--color-text-muted)]"> · {selectedVersion.versionNumber}</span>
            )}
          </span>
          <button
            onClick={() => setSelectedProject(null)}
            title="Zrušiť výber projektu"
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] shrink-0"
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}

      <NavItem
        icon={<IconVersions />}
        label="Verzie"
        active={hasProject ? location.pathname === `/projects/${selectedProject!.slug}` : false}
        onClick={() => navigate(hasProject ? `/projects/${selectedProject!.slug}` : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k verziám"
      />
      {/* E2 (CR-NS-041): per-project Backlog. Project-scoped — disabled (not cross-domain fallback)
          when no project is selected. */}
      <NavItem
        icon={<IconBacklog />}
        label="Zásobník"
        active={hasProject ? isActive(`/projects/${selectedProject!.slug}/backlog`) : false}
        onClick={() => navigate(hasProject ? `/projects/${selectedProject!.slug}/backlog` : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k zásobníku"
      />
      {/* v2 spine STEP 1 (Chrbtica): the Riadiace centrum — the ONE conversation-centred build
          surface that replaces the AI Agent terminal + the 4-phase Vývoj board. Project- +
          version-scoped per design §4.1 — disabled when no project is pinned. The "čaká na Manažéra"
          attention dot moves here (it fired on Vývoj before). */}
      <NavItem
        icon={<IconRiadiace />}
        label="Riadiace centrum"
        active={hasProject ? isActive("/riadiace-centrum") : false}
        onClick={() => navigate(hasProject ? "/riadiace-centrum" : projectsFallback)}
        // Disabled WITH A REASON (never silently, never hidden — the deploy-matrix treatment) when the
        // pinned project's pipeline refuses this user: the screen behind it could only show an empty board.
        disabled={!hasProject || pipelineAccessDenied}
        disabledTitle={
          pipelineAccessDenied
            ? "Nemáš prístup k tomuto projektu — Riadiace centrum môže otvoriť jeho vlastník alebo Manažér. V Projektoch si pripni vlastný projekt."
            : "Vyber projekt pre prístup k Riadiacemu centru"
        }
        badge={hasProject && !pipelineAccessDenied && cockpitAwaiting}
        badgeLabel="čaká na Manažéra"
      />
      {/* ICCINT-167: Poradca — talk with an agent that only reads and advises; runs beside the build. Project item:
          greyed out with a reason without a pinned project or without access, same as the Control Centre. */}
      <NavItem
        icon={<IconPoradca />}
        label="Poradca"
        active={hasProject ? isActive("/poradca") : false}
        onClick={() => navigate(hasProject ? "/poradca" : projectsFallback)}
        disabled={!hasProject || pipelineAccessDenied}
        disabledTitle={
          pipelineAccessDenied
            ? "Nemáš prístup k tomuto projektu — Poradcu k nemu môže otvoriť jeho vlastník alebo Manažér. V Projektoch si pripni vlastný projekt."
            : "Vyber projekt pre prístup k Poradcovi"
        }
      />
      {/* CR-1 (nex-studio-visual): the Vizuál surface — the live app preview (monitor 2), shown while the AI
          edits the FE during the vizual phase. Project-scoped like the other build items → disabled (not
          hidden, per the disabled-over-hidden convention) when no project is pinned. */}
      <NavItem
        icon={<IconVizual />}
        label="Vizuál"
        active={hasProject ? isActive("/vizual") : false}
        onClick={() => navigate(hasProject ? "/vizual" : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k Vizuálu"
      />
      {/* v2 spine STEP 1: Špecifikácia — the read-only spec shell (the agreed .md is wired in a
          later step). Project-scoped — disabled (not hidden) when no project is pinned. */}
      <NavItem
        icon={<IconSpec />}
        label="Dokumenty"
        active={hasProject ? isActive("/specifikacia") : false}
        onClick={() => navigate(hasProject ? "/specifikacia" : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k dokumentom"
      />
      {/* DEV-84: the CI runs of the selected project's GitHub repository — read-only, project-scoped like the rest. */}
      <NavItem
        icon={<IconRepozitar />}
        label="Repozitár"
        active={hasProject ? isActive("/repozitar") : false}
        onClick={() => navigate(hasProject ? "/repozitar" : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k repozitáru"
        badge={hasProject && ciRed}
        badgeTone="danger"
        badgeLabel="Kontroly hlavnej vetvy zlyhali"
      />
      <NavItem
        icon={<IconNsCode />}
        label="NS Code"
        active={hasProject ? isActive("/ns-code") : false}
        onClick={() => navigate(hasProject ? "/ns-code" : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k súborom projektu"
      />
      {/* v2 (CR-V2-019): per-customer deploy surfaces (design §3 / §4.1). Nav items are added now;
          their PAGES land in Milestone G (Zákazníci = CR-V2-025, UAT/PROD = CR-V2-027). Until then
          the routes resolve to a lightweight "pripravuje sa" placeholder (App.tsx) so they never
          404; project-scoped → disabled when no pin. */}
      <NavItem
        icon={<IconCustomers />}
        label="Zákazníci"
        active={hasProject ? isActive("/zakaznici") : false}
        onClick={() => navigate(hasProject ? "/zakaznici" : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k Zákazníkom"
      />
      <NavItem
        icon={<IconUat />}
        label="UAT"
        active={hasProject ? isActive("/uat") : false}
        onClick={() => navigate(hasProject ? "/uat" : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k UAT"
      />
      <NavItem
        icon={<IconProd />}
        label="PROD"
        active={hasProject ? isActive("/prod") : false}
        onClick={() => navigate(hasProject ? "/prod" : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k PROD"
      />
      {/* E5 (CR-NS-044): per-project costs (CR-V2-063 renamed the screen Metriky → Náklady; the ROUTE
          stays `/metrics` — renaming paths is churn). Project-scoped — disabled (not cross-domain
          fallback) when no project is selected. */}
      <NavItem
        icon={<IconMetrics />}
        label="Náklady"
        active={hasProject ? isActive(`/projects/${selectedProject!.slug}/metrics`) : false}
        onClick={() => navigate(hasProject ? `/projects/${selectedProject!.slug}/metrics` : projectsFallback)}
        disabled={!hasProject}
        disabledTitle="Vyber projekt pre prístup k nákladom"
      />

      <NavItem icon={<IconKbBook />} label="Dokumentácia" active={isActive("/kb")} onClick={() => navigate("/kb")} />
      {/* E4 (CR-NS-046): Credentials nav gated to Ri (mirrors the backend JWT-ri restriction + the
          presence toggle gate); nav visibility only. */}
      {user?.role === "ri" && (
        <NavItem icon={<IconKey />} label="Prístupy" active={isActive("/credentials")} onClick={() => navigate("/credentials")} />
      )}

      {/* "Aktualizácie" — user-facing changelog. Sits as the last item of the
          group directly ABOVE the Settings section header (Director directive). */}
      <NavItem icon={<IconUpdates />} label="Aktualizácie" active={isActive("/updates")} onClick={() => navigate("/updates")} />

      {/* DEV-81: a line, not a heading — „Nastavenia“ above „Nastavenia“ said the same thing twice. */}
      <SectionDivider />
      <NavItem icon={<IconSettings />} label="Nastavenia" active={isActive("/settings")} onClick={() => navigate("/settings")} />
    </ShellSidebar>
  );
}

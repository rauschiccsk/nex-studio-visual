# NEX Studio v2.0.0 — AI Agent + Auditor — Design

> Build-ready design for NEX Studio v2.0.0: the 5-role serial build pipeline is replaced by **one strong AI Agent (the doer) + an independent Auditor (the verifier)**, governed by a **Miera autonómie** dial.

This document **consolidates and supersedes** the consultation log at `/opt/projects/nex-studio/docs/architecture/nex-studio-v2-lead-engineer-auditor.md`. It is the build-ready design, dated **2026-06-26**. The body is English; UI labels are Slovak as decided (AI Agent, Vývoj, Zadanie, Špecifikácia, Pravidlá agenta, Miera autonómie, Zákazníci, Manažér, UAT/PROD).

> **Status note.** The consultation was run as a Customer-style walkthrough (Manažér = Zákazník). **Round 1** (basic functions + key differences vs v1) is **CLOSED**. **Round 2** (systematic project-creation → production-deploy walkthrough) is **COMPLETE** (2026-06-26) — the full lifecycle is walked end-to-end and all Open items (§8) are addressed (resolved or consciously deferred to dedicated/build rounds). The design is **build-ready**. Nothing here introduces new design beyond the recorded decisions.

---

## 1. Architecture

### 1.1 Architecture in one line

NEX Studio v2.0.0 is built around **one strong AI Agent** (the doer — it holds full context end-to-end and dynamically spawns ephemeral helpers) **+ an independent Auditor** (the verifier, whose intensity scales with the autonomy dial), governed by a **Miera autonómie** dial (how often the AI Agent stops for the Manažér's approval). This replaces the 5-role serial pipeline.

### 1.2 The three architectural pillars

**1. AI Agent — the doer ("Dedo, productized").**
A single strong senior agent that owns and delivers the whole build with **one warm context, no handoffs** (Príprava → Návrh → Programovanie). It does the core/hard work itself and **dynamically spawns ephemeral helper agents** for parallel/bulk sub-tasks, then integrates the results — exactly how Opus/ultracode operates. Small task → no helpers; large task → it spawns and directs them. It is **not a renamed Coordinator**: the old Coordinator *dispatched* work between fixed roles and carried the "papers"; the AI Agent *does* the work and only pulls in *temporary* helpers on demand. Only the Coordinator's Manažér-facing part survives — reporting status and asking for approval — now done by the AI Agent itself. Under the hood it is a real Claude Code `claude` CLI session in a PTY (the same session kind the Director uses with Dedo), rendered in the browser.

- **Does:** preparation, design, task plan, coding (with continuous self-checking), and directing/integrating its helpers.
- **Does NOT:** its own final independent verification — that belongs to the Auditor, because no agent can fully audit itself. The AI Agent is not its own judge.

**2. Auditor — the independent verifier (the Manažér's proxy when the Manažér is absent).**
A **separate agent, outside the AI Agent's team** (NOT one of its helpers), invoked at high-value points only — not per-task. Its **intensity SCALES with the Miera autonómie**: when autonomy is low and the Manažér is in the loop, the Auditor is light (the Manažér + the AI Agent's self-check + tests *are* the audit, like Dedo); when autonomy is high and the build is unsupervised, the Auditor goes full — it becomes the independent eyes the Manažér would otherwise provide. The Auditor exists precisely to make **unsupervised builds** safe; Dedo can work auditorless today only because the Director is always present.

- **Two touchpoints:** (a) **upfront** spec/design review after Návrh — independently scans the brief + the design for holes, ambiguities and contradictions (the old Customer-agent's Gate-E function, now done by the Auditor); (b) **end** verification at Verifikácia — release-acceptance (run the app, confirm it does what the brief promised) + adversarial spot-checks on risky parts (security, money, core contract).
- **Division of labour:** the Auditor only **finds/verifies**; the **AI Agent fixes**. Implementation problems loop back to the AI Agent (bounded ~5 attempts, then escalate to the Manažér); spec/design holes escalate to the Manažér directly. Independence is preserved at both ends.

**3. Miera autonómie — the autonomy dial.**
A Manažér setting (in Nastavenia) controlling how much the AI Agent does on its own versus how often it stops at a **schvaľovací bod** (approval point) for the Manažér. Presets: **Plná autonómia** · **Len na konci** · **Pri kľúčových bodoch** · **Po každej fáze**. Two stops are **independent of the dial**: the **Špecifikácia approval** at the end of Príprava (ALWAYS mandatory) and **deploy (UAT/PROD)** (ALWAYS a separate, manual, per-customer action). The dial also drives the Auditor's depth and the fast-fix lane (fast-fix = dial at full-auto).

### 1.3 What fundamentally changes

| | v1 (5-role serial pipeline) | v2.0.0 (AI Agent + Auditor) |
|---|---|---|
| Build engine | Designer → Customer → Implementer → Auditor → Coordinator, with serial handoffs | One AI Agent (with on-demand ephemeral helpers) + one independent Auditor |
| Context | Re-read on every handoff → drift | One warm context end-to-end, no handoffs |
| Verification | Auditor checked **every task** (expensive, over-engineered) | Auditor is **targeted**: upfront review + end acceptance + risky-part spot-checks, intensity dial-scaled |
| Approval | Forced gates (the 2d4h Director-wait) | **Schvaľovacie body** chosen by the Miera autonómie dial |
| Doer/dispatcher split | Coordinator dispatched, roles did the work | The AI Agent *does* the work; helpers are temporary, not standing |

**Rationale (the over-engineering it replaces).** Dogfood metrics showed the 5-role pipeline ran at **~1.1× human** (the Programmer role alone at 0.6×, i.e. slower than a human), produced **100+ reworks**, and imposed a **2d4h Director-wait** — over-engineered for the value delivered, and increasingly so as the underlying models strengthen. v2.0.0 keeps the transparency and the safeguards (visible phases, an independent verifier, an acceptance gate) while collapsing the serial relay into a single strong doer plus a targeted, autonomy-scaled checker.

> **Note on naming.** In v2.0.0 the human operator is the **Manažér (Manažér projektu)**. Where this document says "Director" in rationale/Dedo-reality references (the v1 operator, "the Director is always present," the historical "2d4h Director-wait"), that is intentional historical context — the live tool is operated by the Director today; the v2 *design* operator is the Manažér.

### 1.4 Scope of the change

**Only the build ENGINE changes (D1).** The surrounding infrastructure stays and adapts: project creation, the version/work hierarchy, metrics, the cockpit, KB integration, UAT provisioning, deploy machinery, settings, and auth. The build pipeline itself remains **visible as 4 phases — Príprava → Návrh → Programovanie → Verifikácia** (+ Hotovo, terminal) — but the display now shows *which stage the AI Agent is in*, not *which agent is active*. **Deployment is out of the build pipeline** — it is separate and per-customer (each customer has its own UAT + PROD instance and data), reached through the dedicated UAT/PROD tabs rather than as a pipeline phase.

---

## 2. Build pipeline

The **build pipeline** is the customer-agnostic engine that produces and verifies one *version* of the app. It has **four phases — Príprava → Návrh → Programovanie → Verifikácia** (D3). Deployment is **OUT** of the pipeline: it is separate and per-customer (UAT / PROD tabs, D6). The phases are visible as a **horizontal phase bar at the top of the Vývoj board** (`Príprava ✓ › Návrh ● › Programovanie ○ › Verifikácia ○`), each phase a clickable tab with permanent, durable content.

The display shows **which stage the AI Agent is in** (its own progress) — not "which agent is active." The whole build is owned end-to-end by the single **AI Agent** with full warm context (it spawns ephemeral helpers as needed); the **Auditor** is the independent verifier invoked at high-value points, not a phase-by-phase reviewer.

### 2.1 The four phases

**1. Príprava** — the AI Agent turns the amateur **Zadanie** (the free-text brief, `customer-requirements.md`) into the professional **Špecifikácia**.
- Triggered when the Manažér, after saving the Zadanie in the New-Version flow, clicks **"Spustiť tvorbu špecifikácie"**. NEX Studio auto-activates the 👨‍💻 AI Agent tab and injects an init prompt (*"Načítaj zadanie a začni prípravu špecifikácie"*).
- Príprava is an **interactive terminal dialogue** (like a Director↔Dedo session): the AI Agent reads the Zadanie, systematizes the requirements, **asks the Manažér clarifying questions on every unclear / under-thought point — no design begins until every detail is understood** — and **proactively proposes improvements** (features / UX / quality).
- Output = the **Špecifikácia**, rendered as Markdown (.md) in the **🔄 Vývoj → Príprava** tab (the manager's reading view).
- Príprava ends with a hard stop: the Manažér reads the Špecifikácia there and approves via the **"Schváliť špecifikáciu"** button. **This approval is ALWAYS mandatory, independent of the autonomy dial.** Only after it does Návrh proceed.

**2. Návrh** — the AI Agent produces **ONE coherent design document** ("like Dedo"), not a multi-doc tree.
- A single living `.md` with sections **sized to the project** (overview/goal · data model · API/interfaces · BE+FE design — only as much as needed) **plus the task plan (EPIC → FEAT → TASK) as its last part**. The old development-spec + separate BE/FE/API specs is retired; the standalone "Plán" phase is merged into Návrh.
- Produced live in the AI Agent terminal, filling the **Návrh** tab as rendered .md. Depth is the AI Agent's judgment (small → light; complex → thorough).
- Fully **automated per the Miera autonómie**. At the **schvaľovací bod after Návrh** two inputs surface together: the AI Agent's own clarification questions and the **Auditor's upfront spec/design review** (see below). The Manažér fixes/clarifies the brief or approves to proceed (this stop fires only if the dial calls for it).

**3. Programovanie** — the AI Agent writes the code, executing the task plan with **continuous self-checks per task** (its own tests / verification as it goes — like Dedo). The **Programovanie tab is a split view**: live programming activity on the LEFT + the task plan on the RIGHT (watch both at once), with the plan-tree expand/collapse state and level color-coding (EPIC = purple, FEAT = yellow, TASK = blue) preserved.

**4. Verifikácia** — the **Auditor's** phase (the end check; consolidates the old "Kontrola zákazníkom" + "Audit").
- **End verification**: release-acceptance — run the app and confirm it does what the brief promised — plus adversarial spot-checks on the risky parts (security, money, core contract). **NOT per-task.**
- The Auditor's verdict + findings fill the **Verifikácia** tab as a durable record.

Then **Hotovo** (terminal) — the version is built and verified, ready for the separate, per-customer Deploy (UAT/PROD), which is outside this pipeline.

### 2.2 Flow (end to end)

```
Nový projekt → name (→ slug) + type + auth mode + description/owner → "Vytvoriť"
   → NEX Studio auto-scaffolds (repo BE+app-FE + GitHub repo + ports + structure) → empty project

Nová verzia → enter Zadanie (free text) → "Spustiť tvorbu špecifikácie"
   → Príprava: interactive AI Agent dialogue → Špecifikácia (.md)
   → [ALWAYS] "Schváliť špecifikáciu"   ← mandatory, dial-independent
   → Návrh: ONE design doc + task plan (automated per dial)
   → [per dial] "Schváliť návrh"  (+ Auditor upfront review surfaces here)
   → Programovanie: code + per-task self-checks
   → Verifikácia: Auditor end check (release-acceptance + spot-checks)
   → Hotovo
       (Deploy to a customer's UAT/PROD = separate, per-customer, outside the pipeline)
```

### 2.3 Schvaľovacie body and the Miera autonómie

Between phases sit **schvaľovacie body** — the Manažér's potential approval stops. They are **not** the Auditor (which is the independent verifier, not an approval stop). Which schvaľovacie body actually halt is governed by the **Miera autonómie** dial (set in Nastavenia, global default, overridable per project / per build).

**Four levels:**

| Level | Behavior |
|---|---|
| **Plná autonómia** | Runs the whole build non-stop; reports only at the end / if truly stuck. |
| **Len na konci** | Runs autonomously; stops when the build is verified/done. |
| **Pri kľúčových bodoch** | Stops after Návrh, and at build-done. |
| **Po každej fáze** | Stops after each dial-governed phase — Návrh / Programovanie / Verifikácia — for maximum control. |

The dial governs only the stops within **Návrh → Programovanie → Verifikácia**. Two stops are **always outside the dial**:
- The **Špecifikácia approval** (end of Príprava) is **ALWAYS** a stop, regardless of the dial — so even "Po každej fáze" does not add a *dial-controlled* Príprava stop; the Príprava stop is always on.
- **Deploy (UAT / PROD)** is **ALWAYS** a separate, manual, per-customer action — outside the dial (D6).

(Fast-fix = dial at full-auto.)

### 2.4 The Auditor and problem-handling

The Auditor is the **independent conscience at both ends** of the pipeline:
- **(a) Upfront spec/design review** — after Návrh, before coding commits: the Auditor independently scans the brief + the AI Agent's design for holes / ambiguities / contradictions (the old Customer agent's Gate-E function, now done by the independent Auditor). It surfaces at the schvaľovací bod after Návrh, alongside the AI Agent's own clarification questions.
- **(b) End verification** — at the Verifikácia phase (release-acceptance + adversarial spot-checks, not per-task).

**Auditor intensity scales with the Miera autonómie / Manažér's presence:** low autonomy (Manažér in the loop) → Auditor light (the Manažér + the AI Agent's self-checks + tests are the audit); high autonomy (unsupervised) → Auditor full (the Manažér's independent proxy). The Auditor exists precisely to enable unsupervised builds.

**When the Auditor finds a problem** — the Auditor only **finds/verifies**; the AI Agent **fixes** (independence preserved):
- **(i) Implementation problem** (bug / code doesn't match spec / behavioral failure) → the **AI Agent fixes** → Auditor **re-verifies** → **bounded loop (~5 attempts, configurable)**. If still unfixable (or beyond a code fix) → **STOP + escalate to the Manažér**.
- **(ii) Spec/design hole** (missing or ambiguous information) → **escalate to the Manažér directly** (clarify → revise the Špecifikácia / Návrh).

### 2.5 Fast-fix lane ("Rýchla oprava")

KEPT, simplified. For small fixes that do not warrant a full version build (no Zadanie/Špecifikácia, no full pipeline):
- Describe the fix (a **directive**) → **auto-create a patch version** (vX.Y.Z+1) → the **AI Agent** takes the directive (which IS the brief) and fixes it on a **short path** (skips the heavy Návrh) → **light Auditor check** (fix works + no regression — focused, not the full release oracle) → **auto-deploy**.
- **Autonomous** (zero mid-flight approvals by default; dial-able to require approval for sensitive fixes). It is its own minimal lane (Oprava → quick verify → Deploy), outside the build pipeline. Proven today: nex-ledger fast-fix ~18 min, zero approvals.

---

## 3. Deploy and Customers

> Source decision: **D6** (Deploy & Customers model) plus the sidebar (👥 Zákazníci · 🧪 UAT · 🚀 PROD), the autonomy-dial carve-out (deploy is always manual + per-customer, outside the dial), and the Verifikácia/Auditor boundary.

### 3.1 Principle: deploy is OUT of the build pipeline, and PER-CUSTOMER

The build pipeline (**Príprava → Návrh → Programovanie → Verifikácia**) is **customer-agnostic**: it produces and verifies a *version* of the app, using its own internal test fixtures. It never deploys anything.

**Deployment is a separate concern, done per customer.** Each customer runs its **own instance** with its **own data** — the proven instance-per-customer model (ANDROS / ICC / MÁGERSTAV for nex-inbox, `/opt/customers/<slug>/`). There is no single universal UAT or PROD environment for a project; there is one UAT instance and one PROD instance **per customer**.

This cleanly separates two questions:
- *"Is the version correct?"* → answered once, customer-agnostically, by **Verifikácia** (the Auditor) inside the pipeline.
- *"Is this version deployed and accepted for a given customer?"* → answered per customer, outside the pipeline, in the UAT/PROD tabs.

### 3.2 "Zákazníci" — the per-project customer registry

A new project-scoped sidebar tab **👥 Zákazníci** holds the registry of customers that use the app. Customers are **added via a form**, capturing:

- name and **slug**
- **subdomain** (the customer's URL)
- **integrations** (per-customer external systems)
- **secrets** (per-customer credentials; handled as secrets — never echoed, stored outside source, per §4/§5)
- deploy target = the customer's **own UAT + PROD instance / DB / data**

This productizes today's `onboard-customer.sh` plus the auto-UAT-provisioning machinery, surfaced in the UI.

**Uniform structure — internal apps are treated identically.** There is no special "internal" case: for an internal app, the "customer" is simply **ICC s.r.o.**, registered through the same form as any external customer. One code path, no branches.

### 3.3 UAT and PROD tabs — the version × customer matrix

Two new project-scoped sidebar tabs sit below Zákazníci:

- **🧪 UAT** — lists the project's customers and which version each runs on its **test instance** (test data).
- **🚀 PROD** — lists the project's customers and which version each runs in **production**.

Each tab is a **version × customer matrix**: per customer, the currently deployed version plus the action to deploy a different (verified) one. Because deployment is per customer, **different customers may run different versions** at the same time (e.g. ANDROS PROD on `v1.0.0`, ICC on `v1.1.0`).

### 3.4 Deploy flow

The **Manažér** drives deployment manually, per customer. Deploy is **always a separate, manual, per-customer action — outside the autonomy dial** (the dial governs only the build phases).

1. Open the **UAT** (or **PROD**) tab.
2. Pick a **customer** + a **verified version**.
3. Click **"Nasadiť"**.
4. The system **auto-provisions / updates that customer's own instance** with that version — its own DB, subdomain, and integrations, on its own URL.

Deploy is automated per-customer instance provisioning; the Manažér supplies only the customer + version choice.

### 3.5 UAT acceptance gate — opens PROD per customer

PROD is gated behind a **human acceptance of the customer's UAT** — never bypassed.

The Auditor's automatic **Verifikácia** already passed inside the pipeline before any deploy; the UAT gate adds the **Manažér's** human acceptance against the real instance:

1. The version is deployed to the customer's UAT (their test instance + test data) via **"Nasadiť"**.
2. In the **UAT** tab, per customer, there is a **link to the UAT URL** plus an **"Akceptovať"** button. The **Manažér tests the customer's UAT on its URL**.
3. On **"Akceptovať"**, the version is marked **accepted-for-PROD for that customer**, which **opens the PROD deploy** for them.
4. Acceptance is **per customer**, independent, and **logged** (who / when / version / customer).

The customer **may** be given UAT access to try it too, but the **Manažér performs the acceptance**. **No PROD deploy without UAT acceptance** — this is the per-customer acceptance gate, and it is never bypassed.

### 3.6 Versioning across deploys

- First version produced by the pipeline = **`v0.1.0`** (base / initial dev version).
- The **first production Deploy** of a project bumps it to **`v1.0.0`**.
- From there, each customer advances independently; the PROD tab reflects which version each customer actually runs.

### 3.7 Data and secrets — fresh first, preserved forever after

The data lifecycle of an instance is explicit and non-negotiable:

- **First install of a new project/customer = a fresh, empty instance** — no seed data, no test data. Data accumulates through real use: in UAT you test by *using* it; in PROD real data grows.
- **Every version update to an EXISTING instance MUST PRESERVE the accumulated data and secrets.** An update means: update the code + **run schema migrations**. It **never wipes data and never rotates secrets**.

In short: **first deploy = empty; every later deploy = data-preserving**, with migrations carrying the existing data forward. (This is the hard-learned inbox-UAT-redeploy lesson — data and secrets must survive an update — encoded as a rule.)

---

## 4. UI

> This section specifies the NEX Studio v2.0.0 user interface: the final left sidebar, project creation, the key screens (👨‍💻 AI Agent, 🔄 Vývoj), the task-plan UX, and the contents of ⚙️ Nastavenia. The body is English; UI labels are Slovak as decided. The human operator role is **Manažér projektu** (Project Manager) throughout.

### 4.1 Left sidebar (FINAL)

A single left navigation rail, top-to-bottom. The **📖 Špecifikácie** tab is **removed** — the spec and design docs now live inside the 🔄 Vývoj phase tabs (Príprava = Špecifikácia, Návrh = design document), per version and persistent.

| Icon | Label (SK) | Scope | Purpose |
|---|---|---|---|
| 🏠 | **Prehľad** | global | Landing / overview. |
| 📁 | **Projekty** | global | Project list + create (Nový projekt). Carries the **📌 selected project · version** indicator (the active pin). |
| 🌿 | **Verzie** | project-scoped | Version list + create (Nová verzia → Zadanie). |
| 📋 | **Zásobník** | project-scoped | Backlog. |
| 👨‍💻 | **AI Agent** | project-scoped | The doer's live terminal — the Claude Code session (was *AG Koordinátor*). |
| 🔄 | **Vývoj** | version-scoped | The build board (was *Orchestrácia*). |
| 👥 | **Zákazníci** | project-scoped | Per-project customer registry (form-managed). |
| 🧪 | **UAT** | project-scoped | Per-customer UAT deploy + acceptance (version × customer). |
| 🚀 | **PROD** | project-scoped | Per-customer PROD deploy (version × customer). |
| 📊 | **Metriky** | project-scoped | Metrics & ROI (per-phase basis). |
| 📚 | **Dokumentácia** | global | KB. |
| 🔑 | **Prístupy** | global | Credentials (Manažér). |
| ✨ | **Aktualizácie** | global | NEX Studio's own updates / changelog. |
| ⚙️ | **Nastavenia** | global | Settings, incl. **Miera autonómie**. |

**Footer:** presence indicator **🟢/🌙** (Manažér presence toggle) + the user card.

**Rules:**
- **Project-scoped items are disabled when no project is selected** (rendered greyed + tooltip, per the disabled-over-hidden convention — never removed from view). They become active once a project is pinned via 📁 Projekty.
- The **📌 selected project · version** pin appears under 📁 Projekty and anchors every project- and version-scoped item.
- **The Auditor has no nav item** — its verdict and findings surface inside 🔄 Vývoj → Verifikácia.
- Icons: AI Agent = 👨‍💻 (🤖 also fits); Vývoj = 🔄 (fixed).

### 4.2 Project creation and archetypes

> Source: D1 (project creation stays) + the part-by-part "Project types / archetypes" and "R2 create flow" decisions. The archetype model is the v2.0.0 replacement for the old single/multi-module choice (D7); it is **still being finalized** (Mobil deferred — see Open items).

**A project = one backend + one-or-more frontend "surfaces."** The old single/multi-module choice is dropped; instead the project *type* is a preset composition (a **scaffold template**) the AI Agent uses to start. nex-shared holds the shared solutions across surfaces.

**Final type list:**

| Type | Composition | Notes |
|---|---|---|
| **(1) Štandardný** | BE + app-FE | The default app shape (NEX Ledger / Inbox). |
| **(2) Web** | BE + admin-FE + public site | A *managed/monitored* site (not a throwaway static gen): the admin-FE configures the site and shows its metrics. **Optional eshop/commerce** = turning ON cart / checkout / payments + **bidirectional IS-integration** — the latter is the real complexity, to be designed carefully. (Web and the former "Eshop" are merged into one archetype: same shape → DRY, commerce is an add-on capability.) |
| **(+) Mobil** | a mobile surface addable to any project | Shares the BE. **Deferred to a dedicated design round** (toolchain, build/test, Auditor verification — see Open items). |

**Archetypes = scaffold templates.** Each type maps to a starting structure the AI Agent scaffolds; nex-shared provides the shared web-platform / cross-surface solutions.

**New-Project form (Standard project).** Projekty → **"Nový projekt"** → enter:
- **name** (→ slug)
- **type** (Štandardný / Web)
- **auth mode** — **MANDATORY field** — **password-login like Studio / token-launch like Inbox** (the E1 two modes). This is **always chosen at project creation** because it shapes the project's auth structure (BE login + FE login flow).
- **description / owner** (owner used for notifications)

On **submit**, NEX Studio **auto-scaffolds**: repo (BE + app-FE) + GitHub repo + port allocation + structure → an **empty project, ready**. Next step: create the first version and enter the customer specification (the Zadanie). The deploy target is supplied later, at the deploy step (per-customer).

### 4.3 Version creation and the Zadanie

> Source: the part-by-part "Version / work hierarchy" + "R2 create-version flow" decisions.

**Verzie → "Nová verzia"** → enter:
- **version number** (a suggested default for the first version, editable)
- the **"Zadanie"** — the brief, the main input. The Zadanie editor is **free text** — a recorded decision, chosen over a guided-template alternative.
- optional title

On save: the version record is created and the spec is written to `docs/specs/versions/v<N>/customer-requirements.md` automatically. **No autopilot** — after saving the Zadanie, the Manažér clicks **"Spustiť tvorbu špecifikácie"** to begin the Príprava phase (see Build pipeline §2.1).

**Terminology:** *Zadanie* = the assignment/brief (amateur input; file `customer-requirements.md`); *Špecifikácia* = the professional spec produced by the Príprava dialogue.

### 4.4 Key screens

#### 4.4.1 👨‍💻 AI Agent — the doer's terminal

The AI Agent tab is a **Claude Code `claude` CLI session running in a PTY** — the same kind of session the Director uses with Dedo — rendered in the browser and wrapped in chrome. It is the intimate console where the Manažér watches and talks to the doer live. Layout, top to bottom:

- **Header** — agent name + status: **Voľný** / **Pracuje na &lt;projekt&gt; v&lt;ver&gt; — fáza X** / **Čaká na súhlas**.
- **Thin 4-phase strip** — a compact mirror of the Vývoj phase bar (→ links to 🔄 Vývoj).
- **Live activity console** — scrollable, durable PTY (the raw session).
- **Helpers panel** — appears when the AI Agent spawns ephemeral helper agents: **"+ N pomocníci"** with a one-line description of what each is doing. Hidden when none are active.
- **Input box** — to consult, direct, or answer the agent.

**Behaviour:**
- **Idle** → ad-hoc consultation with the agent.
- **Building** → watch live + answer **schvaľovacie body** inline (these are also flagged by the *"čaká na Manažéra"* badge in 🔄 Vývoj).
- Project-scoped (follows the pin).

> **Vývoj vs AI Agent:** 🔄 Vývoj = the manager view (overview + approvals); 👨‍💻 AI Agent = the raw terminal (watch/talk live).

#### 4.4.2 🔄 Vývoj — the build board

Version-scoped board for the selected version. The pipeline is a **horizontal phase bar at the TOP** (like NEX Command — not a second left rail; saves horizontal space):

```
Príprava ✓ › Návrh ● › Programovanie ○ › Verifikácia ○
```

States: **✓ done · ● current · ○ pending**. Each phase chip is **clickable**.

**The phase chips ARE the tabs — there is NO separate tab row.** Clicking a phase shows that phase's artifacts + activity. Each phase tab has **permanent content that persists after the build completes** (this fixes the old pain where the task plan was visible only during the build, then vanished). Current phase = live; finished phases = a durable record.

**Two coexisting states on the bar:**
- **● = where the build currently is** (auto-advances as the AI Agent progresses).
- **highlighted = which tab the Manažér is viewing** (their click).

These can differ — e.g. review the finished **Návrh** tab while the build runs ahead in **Programovanie**.

**Tab contents (kept forever, per version):**

| Tab | Content |
|---|---|
| **Príprava** | Zadanie → **Špecifikácia** — the approved spec, rendered as Markdown (the manager's reading view). Carries the **"Schváliť špecifikáciu"** button (this approval is ALWAYS mandatory, independent of the autonomy dial). |
| **Návrh** | The **one coherent design document** (rendered .md), including the **task plan** (EPIC → FEAT → TASK) as its last part. |
| **Programovanie** | Coding log + task progress (split view — see §4.5). |
| **Verifikácia** | The **Auditor's** verdict + findings. |

**Below the tabs:**
- **Who's-up status** — AI Agent / + helpers / Auditor / *čaká na Manažéra*.
- **Action buttons at schvaľovacie body** — **Schváliť** / **Uprav** / **Pokračovať**, plus **Spustiť**. Which stops actually halt is governed by the **Miera autonómie** (Nastavenia).
- **Raw-terminal peek** — a drawer to glance at the raw AI Agent terminal (the full console is the 👨‍💻 AI Agent tab).

**Persistence:** each phase stores its output as a durable artifact/record (Špecifikácia, design document, coding log, verdict) — not in-memory only. This doubles as the build's audit trail.

### 4.5 Task plan UX (lives in Návrh)

The task plan (EPIC → FEAT → TASK) is the last part of the **Návrh** design document. Its tree view has three requirements:

1. **Remember expand/collapse state.** Navigating away (e.g. to 📊 Metriky) and back must NOT reset the tree — it persists across navigation, and ideally across page reload (per user). (Today it resets — a bug to fix.)
2. **Colour-code the levels** for readability in large plans:
   - **EPIC = purple**
   - **FEAT = yellow**
   - **TASK = blue**
   (Legibility to be verified in practice — especially yellow on the light theme; shade may be tuned.)
3. **Split view during Programovanie.** In the Programovanie tab the screen splits: **live programming activity on the LEFT + the task plan on the RIGHT** — both watched at once.

### 4.6 ⚙️ Nastavenia (Settings)

The Settings screen holds the global configuration. Confirmed contents (further details **TBD** — see Open items):

- **Miera autonómie** — the autonomy dial. Controls how often the AI Agent stops at a **schvaľovací bod** for approval, with preset levels: **Plná autonómia · Len na konci · Pri kľúčových bodoch · Po každej fáze**. Global default, overridable per project / per build.
  - Two exceptions are always outside the dial: the **Špecifikácia approval** (end of Príprava) is ALWAYS a stop; **deploy (UAT/PROD)** is ALWAYS a separate, manual, per-customer action.
- **AI model + effort** — model and effort level for the agents.
- **Credentials** — credential configuration.
- **Metriky — per-PHASE rates / wages** — agent rates and human wages set per phase (Príprava → Návrh → Programovanie → Verifikácia), feeding the per-phase Metrics & ROI computation.

Remaining items (Director, 2026-06-26) all **keep their current NEX Studio implementation** (they work well):
- **Users & roles** — as today (add users, assign roles); the operator role label "Director" → **"Manažér"**.
- **Cesty a šablóny** — default project source/KB path templates used at project creation (e.g. `/opt/projects/{slug}`, `{slug}` auto-filled); admin/infra defaults per ICC structure, rarely touched.
- **Notifikácie (Telegram)** — as today (per-project owner/chat_id + the presence toggle).

---

## 5. Agent rules, KB, memory, comms

This section defines the **rules** the two v2.0.0 agents run under (the term **"Pravidlá agenta"** replaces the old "charta/charters"), how they use the **Knowledge Base and memory** ("exactly like Dedo"), and the **simplified communications** that replace the retired 5-role file-bus.

### 5.1 Pravidlá agenta (agent rules)

v2.0.0 has two agents — the **AI Agent** (the doer/builder) and the independent **Auditor** (the verifier) — so the five old role-charters collapse into **two sets of Pravidlá agenta plus one shared base**. The mechanism is unchanged from today (a rules-file injected as the agent's system prompt); there are now **2 sets instead of 5**. Detailed wording is written during the build; this section fixes the structure and intent.

#### Shared base (applies to both agents)

- **Security §4** — the inviolable P0 rules: never output / write / commit / push credentials; secrets live only in `.env` or runtime env; `VITE_*` is public-only.
- **ICC standards** — coding conventions, structure, naming, schema governance — the shared ICC ground truth.
- **Communication** — Slovak with the Manažér, tykanie, concise (quality over quantity); English identifiers in source, Slovak only in UI strings; report own findings, never expectations ("seems to work" is forbidden).

#### (1) AI Agent rules — "build like a great ICC engineer (like Dedo)"

How the doer agent works end-to-end across Príprava → Návrh → Programovanie:

- **Read first** — load the brief (`customer-requirements.md`), existing code, specs, and the KB before proposing anything (the "read before you think" principle).
- **Ask until understood** — in Príprava, systematize the Zadanie and ask the Manažér clarifying questions on every unclear / under-thought point. **No design until every detail is understood.**
- **Propose improvements** — proactively suggest features / UX / quality upgrades; the professional takes responsibility for the result, the amateur input is only the starting point (waterfall philosophy).
- **Self-check** — continuous self-verification while coding; the AI Agent is its own first line of quality, but **never its own final judge** (that is the Auditor — see D2/D5).
- **Quality-first** — one best long-term solution by default; minimal / MVP / stub is never the default recommendation.
- **Waterfall** — plan thoroughly before coding; the Špecifikácia is settled and **approved** before implementation begins.
- **KB + own-memory** — read the KB for conventions and lessons; write to and recall from its own persistent per-project memory (see below).
- **Spawning helpers** — direct ephemeral helper agents for parallel/bulk sub-tasks and integrate their results (helpers are internal, not standing roles).
- **Talking to the Manažér** — report status, raise clarification questions, and stop at the **schvaľovacie body** per the autonomy dial.

#### (2) Auditor rules — "verify independently & rigorously"

How the verifier works at its two touchpoints (upfront spec review + end verification):

- **Independence** — the Auditor checks from **outside** the AI Agent's team; it is not one of its helpers. No agent can fully audit itself (blind-spot safeguard).
- **Adversarial / skeptical** — actively hunt for holes, contradictions, and risky assumptions rather than confirm the happy path.
- **Verify-don't-trust** — confirm claims against the artifacts and the running app, not against the AI Agent's say-so.
- **Behavioural acceptance** — at Verifikácia, run the app and confirm it does what the brief promised (release-acceptance), plus adversarial spot-checks on risky parts (security, money, core contract). **Targeted, not per-task.**
- **Upfront spec-completeness** — after Návrh, independently scan the brief + the design for holes / ambiguities / contradictions (the old Customer agent's Gate-E function, now the Auditor's early review).
- **The fix-loop** — the Auditor only **finds / verifies**; the AI Agent **fixes** (independence preserved):
  - *Implementation problem* (bug / spec mismatch / behavioural failure) → AI Agent fixes → Auditor re-verifies → bounded loop (~5 attempts, configurable); if still unfixable or beyond a code fix → **STOP + escalate to the Manažér**.
  - *Spec / design hole* (missing / ambiguous info) → **escalate to the Manažér directly** to clarify and revise the Špecifikácia / Návrh.
- **Security verification** — explicitly verify the §4 hard rules hold in code and at runtime.
- **Dial-able depth** — full independent review for important / regulated projects; lighter (lean on the AI Agent's own questions + self-check + tests) for quick, supervised ones. The Auditor exists precisely to enable **unsupervised** builds — the Manažér's independent proxy when the Manažér is not in the loop.

### 5.2 KB and memory ("exactly like Dedo")

The AI Agent integrates with knowledge on three distinct levels, each with its own write discipline:

1. **Reads the KB** — ICC standards / decisions / lessons / patterns plus project-specific docs, for conventions and for applying past lessons. Access is via **RAG (Qdrant + Ollama embeddings) + direct file reading**. Read access is broad and free.

2. **Own persistent per-project memory (NEW capability)** — the AI Agent has its **own memory** that today's NEX Studio lacks. It **writes freely** — decisions, lessons, context, and Manažér feedback — and **recalls** that memory on future builds of the same project. This is how the agent **learns and retains across builds**, mirroring how Dedo's memory works. Project status / history live-docs fold into this memory together with the Vývoj phase-tabs.

3. **Contributes to the shared ICC KB deliberately** — only **broadly-valuable** lessons and patterns are promoted to the shared ICC KB (to keep it clean), and **every shared-KB write is followed by a RAG reindex** so the vector store never drifts from the filesystem.

> **Write-discipline summary:** read freely · own-memory writes free · shared-KB writes deliberate (+ reindex).

### 5.3 Communications (simplified)

The old **file-bus (`.dedo-channel`)** — the 5-role pipeline messages and hub-and-spoke routing through the Coordinator — is **RETIRED.** With only the AI Agent + Auditor, there are no five roles to bus between. The channels are now:

- **Manažér ↔ AI Agent** — direct, via the **AI Agent terminal** (a live Claude Code session, like a Dedo session), plus **Telegram notify** when the Manažér is away (presence toggle).
- **AI Agent ↔ Auditor** — the Auditor returns its **verdict / findings** into the fix-loop; the result is shown on the **Vývoj** board (Verifikácia tab).
- **AI Agent ↔ helpers** — **internal** only; helpers are orchestrated like a workflow and their results integrated. No standing bus.
- **Notifications** — system → Manažér, for *away / escalation / done* events.

**Audit trail of "who said what"** is no longer a separate comms file-bus. It is the combination of:

- the **terminal's durable log** (the AI Agent console PTY persists),
- the **Vývoj phase tabs** (Príprava / Návrh / Programovanie / Verifikácia, each storing its artifacts permanently), and
- the **audit log** (per-customer acceptance and deploy events: who / when / version / customer).

---

## 6. What changes from v1 (removals & cleanups)

v2.0.0 keeps the surrounding infrastructure intact and changes **only the build engine** (D1). The following are the deliberate removals and cleanups relative to v1.0.0. Each line is a confirmed decision from the v2 consultation, not a new design.

| Area | v1.0.0 | v2.0.0 | Why |
|---|---|---|---|
| **Build team** | 5 standing roles in a serial relay — Designer → Customer → Implementer → Auditor → Coordinator, with handoffs | **AI Agent + Auditor** | One strong senior agent does design + implementation + self-check with full context (spawns ephemeral helpers on demand); the Auditor stays independent (no agent fully audits itself). The Coordinator was a dispatcher; the AI Agent *does the work*. (D2, D4, D5) |
| **Project shape** | single / multi-module choice; `ProjectModule`, `ModuleDependency`, `Epic.module_id` + MM pages (Prehľad / Mapa modulov / Mapa závislostí) | **Multi-module DROPPED** — every project is single, with an archetype/surfaces model (§4.2) | Multi-module-in-one-project was the old way to unify design/DB/solutions but hit a project-size wall. **nex-shared** (versioned shared lib across separate projects) now does that unification without the monolith. Deliberate reversal of the prior "multi-module is CORE" decision. (Assumptions confirmed 2026-06-26 — see §8.) |
| **Spec / design docs nav** | dedicated **📖 Špecifikácie** sidebar tab + deep file-browser path to edit the brief | **Špecifikácie tab removed** — docs live in the **Vývoj** phase tabs (Príprava = Špecifikácia, Návrh = design doc), per version, persistent | Removes the unintuitive browse path; the spec + design now live where they're produced and stay durable. The Špecifikácie browser as a doc surface is retired; the brief is entered inline in the New-Version flow. |
| **Design output** | heavy multi-doc tree: `development-spec.md` + separate BE / FE / API specs | **ONE coherent design document** (`.md`), sections sized to the project, with the task plan (EPIC→FEAT→TASK) as its last part | "Like Dedo" — one living design doc, depth = the AI Agent's judgment, no handoff-driven doc fan-out. |
| **Agent comms** | file-bus (`.dedo-channel`, 5-role pipeline messages, hub-and-spoke via Coordinator) | **File-bus retired** | No 5 roles left to bus between. Manažér ↔ AI Agent is direct via the terminal (+ Telegram when away); AI Agent ↔ Auditor returns a verdict; helpers are internal. Audit trail = terminal log + phase tabs + audit log. |
| **Metrics basis** | per-**role** (Designer/Customer/Implementer/Auditor/Coordinator) | **per-PHASE** (Príprava → Návrh → Programovanie → Verifikácia) | No fixed roles to attribute to. Each cost source maps to the phase it ran in (AI Agent → current phase, helpers → spawning phase, Auditor → Verifikácia). Historical v1 per-role data is not 1:1 comparable. |
| **Operator name** | "Director" | **"Manažér" (Manažér projektu)** | Propagates across the UI — approvals, "čaká na manažéra", autonomy-dial owner, UAT acceptance. |
| **Build board name** | "Orchestrácia" | **"Vývoj"** | Renamed sidebar tab (🔄); the doer terminal tab is **AI Agent** (👨‍💻, was "AG Koordinátor"). |
| **Phase map** | 6 phases | **4 phases** — Príprava → Návrh → Programovanie → Verifikácia | **Plán merged into Návrh** as its last part (the task plan); the old **Vydanie removed** — deployment is now separate & per-customer, outside the pipeline (D3, D6). "Gate" terminology retired in favour of **schvaľovacie body** (autonomy-dial approval stops) vs the independent **Auditor**. |

Two invariants survive all of the above, independent of the autonomy dial:
- the **Špecifikácia approval** at the end of Príprava is **always mandatory** (D3);
- **deploy (UAT/PROD)** is **always a separate, manual, per-customer action** outside the dial (D6).

---

## 7. Build plan

**Keep the infrastructure, replace only the engine (D1).** The cockpit, KB integration, UAT-provisioning, deploy machinery, metrics, and the fast-fix lane all **stay and adapt**. What is rebuilt is the **build ENGINE** — the 5-role serial pipeline becomes the **AI Agent + Auditor** model. Everything above the engine is reused, not rewritten.

**Develop v2.0.0 on a branch; main stays frozen at v1.0.0.**
- CI deploys **only `main`**, and NEX Studio is **host-coupled + self-deploying via CI** — so an unfinished v2 on `main` would self-deploy over the working tool.
- Therefore v2.0.0 is built on a **branch**, leaving `main` frozen at the working v1.0.0 throughout.
- This is also why the **proper NEX Studio self-deploy redesign — tag-based prod / dev / UAT** — is **in v2.0.0 scope but its design is still TBD** (see Open items): the host-coupling + CI-self-deploy reality means the deploy story must be redesigned to safely run a dev branch alongside the live tool, rather than the current "main = the only deployable" assumption.

**Validate cheaply before committing.**
- Before merging v2 to `main`, run **one comparison build** end-to-end with the new engine — a single, low-cost validation that the AI Agent + Auditor model produces a correct app through the 4 phases (Príprava → Návrh → Programovanie → Verifikácia) and a per-customer deploy.
- This mirrors the dogfood approach already proven (NEX Manager / NEX Asistent autonomous builds) but at the cost of **one build**, not a heavy parallel effort — the inexpensive checkpoint before the v1→v2 cutover.

**Cutover.** Only after the comparison build validates the engine does v2.0.0 merge to `main` and assume the live host (under the new tag-based self-deploy), retiring the v1 5-role pipeline.

---

## 8. Open items

**Status (2026-06-26): all six addressed.** Four are RESOLVED (#2 multi-module assumptions, #4 project-docs, #5 Settings details, #6 auth/users); two are consciously DEFERRED to dedicated/build rounds (#1 Mobil, #3 NEX Studio self-deploy) — in scope, not gaps. **No open blocking gaps remain.**

1. **Mobil archetype = biggest unknown → dedicated design round.** The mobile toolchain (RN / Flutter / Expo), how NEX Studio **builds and TESTS** a mobile app (emulator / device), and how the **Auditor verifies** it are all deferred to a dedicated design round. The "(+) Mobil" surface (§4.2) is recorded but not specified. **DEFERRED (Director, 2026-06-26)** to a dedicated design round when the first mobile project is real — not designed speculatively now.

2. **Multi-module-drop assumptions — RESOLVED (Director, 2026-06-26).** Both confirmed: (a) **nex-shared grows to cover BE / DB / solution unification** (not just FE chrome); (b) **NEX Automat = separate single projects sharing nex-shared + API integration** (NOT a multi-module monolith). The multi-module drop (§6) is now fully settled.

3. **NEX Studio's own tag-based self-deploy redesign — DEFERRED to the build phase (Director, 2026-06-26).** In v2.0.0 scope (§7); the detailed tag-based prod / dev / UAT deploy design for NEX Studio itself is co-designed with the new engine during the build (at cutover) — not specified now.

4. **Project specs / docs — RESOLVED (Director, 2026-06-26): no separate surface.** Per-version docs (Zadanie / Špecifikácia / Návrh) live in the Vývoj phase tabs (select the version); ICC/KB docs in Dokumentácia; repo-internal files are the AI Agent's/repo's concern. The Špecifikácie-tab removal needs no replacement.

5. **Settings details — RESOLVED (Director, 2026-06-26).** Beyond Miera autonómie / AI model+effort / credentials / per-phase metrics, the remaining items all **keep their current NEX Studio implementation**: **Users & roles** (works well; role label Director → Manažér), **Cesty a šablóny** (default project source/KB path templates, `/opt/projects/{slug}` style — admin/infra defaults), **Telegram notifications** (per-project owner/chat_id + presence toggle).

6. **Auth / users — RESOLVED (Director, 2026-06-26).** NEX Studio's own login + user-management + roles **keep their current implementation** (works well); only the operator role label changes (**Director → Manažér**). (The built app's auth MODE — password/token — is separate and settled at create-project, §4.2.)

> Round 2 of the consultation is COMPLETE (2026-06-26): the full lifecycle is walked and all six open items are addressed (four resolved, two consciously deferred to dedicated/build rounds). The design is build-ready.

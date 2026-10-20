/**
 * DEV-62 — the data every page of the cockpit is rendered with in the browser guard.
 *
 * Realistic on purpose: a page fed with empty lists shows its empty state, and the tables, cards and long names
 * that break a narrow screen would never be measured. One project (NEX Inbox) with one version in build waiting on
 * the manager's decision, one customer with installations on UAT and PROD, costs, users and settings.
 */

import type { components } from "../src/services/api/pipeline.generated";
import type { AdoptionPreview } from "../src/services/api/deploy";
import type { AdoptableCandidate, GitStatus, NexsharedStatus } from "../src/services/api/projects";
import type { KnowledgeDoc } from "../src/types/knowledge";
import type { TaskPlanResponse } from "../src/types/task-plan";
import { answer, answerUntyped, override, type Override } from "./api";

type S = components["schemas"];

const T0 = "2026-10-01T08:00:00Z";
const T1 = "2026-10-11T14:30:00Z";
const T2 = "2026-10-12T07:45:00Z";

export const USER: S["AuthUser"] = {
  id: "00000000-0000-4000-8000-000000000001",
  username: "manazer",
  email: "maria.manazerova@example.com",
  first_name: "Mária",
  last_name: "Manažérová",
  role: "ri",
  is_active: true,
  must_change_password: false,
  telegram_chat_id: null,
  created_at: T0,
  updated_at: T0,
};

const COLLEAGUE: S["UserRead"] = {
  id: "00000000-0000-4000-8000-000000000002",
  username: "technik",
  email: "technicky.timovy.pracovnik@example.com",
  first_name: "Tibor",
  last_name: "Technický",
  role: "ha",
  is_active: true,
  telegram_chat_id: "123456789",
  created_at: T0,
  updated_at: T1,
};

export const PROJECT: S["ProjectRead"] = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "NEX Inbox",
  slug: "nex-inbox",
  type: "standard",
  auth_mode: "token",
  description: "Spracovanie dodávateľských faktúr z e-mailu do NEX Genesis ako validné Peppol BIS UBL XML",
  status: "active",
  backend_port: 9100,
  frontend_port: 9101,
  db_port: 9102,
  repo_url: "https://github.com/rauschiccsk/nex-inbox",
  source_path: "/opt/projects/nex-inbox",
  kb_path: null,
  guardian_enabled: true,
  setup_warnings: [],
  custom_development_enabled: false,
  private_network: false,
  created_by: USER.id,
  owner_id: USER.id,
  has_prod_deploy: true,
  created_at: T0,
  updated_at: T1,
};

export const VERSION: S["VersionRead"] = {
  id: "22222222-2222-4222-8222-222222222222",
  project_id: PROJECT.id,
  version_number: "1.8.0",
  name: "Doručovanie do Genesisu a poznámky k vydaniu",
  description: "Nová verzia so spoľahlivým doručovaním faktúr do zdieľaného priečinka NEX Genesis",
  status: "active",
  epic_count: 6,
  epics_done: 4,
  bug_count: 1,
  target_date: "2026-10-20",
  release_date: null,
  created_at: T0,
  updated_at: T1,
};

const RELEASED: S["VersionRead"] = {
  ...VERSION,
  id: "22222222-2222-4222-8222-222222222221",
  version_number: "1.7.1",
  name: "Rýchla oprava brány vydania",
  status: "released",
  epics_done: 6,
  bug_count: 0,
  release_date: "2026-10-11",
};

/** What a signed-in browser keeps: the session token and the pinned project and version. */
export const STORAGE: Record<string, unknown> = {
  nex_studio_token: "guard.token.dev62",
  "nex-auth": { state: { token: "guard.token.dev62", user: USER }, version: 0 },
  [`nex-active-context:${USER.username}`]: {
    state: {
      selectedProject: { slug: PROJECT.slug, name: PROJECT.name },
      selectedVersion: { versionId: VERSION.id, versionNumber: VERSION.version_number },
    },
    version: 0,
  },
  // DEV-83: Poradca reopens the conversation it remembers, so its answer with marked choices is measured too.
  [`nex.poradca.open.${PROJECT.slug}`]: "99999999-9999-4999-8999-999999999991",
};

const PORADCA_CONVERSATION = {
  id: "99999999-9999-4999-8999-999999999991",
  project_id: PROJECT.id,
  version_id: VERSION.id,
  version_number: VERSION.version_number,
  author_id: USER.id,
  author_name: "Mária Manažérová",
  title: "Prečo faktúra Kinekus nemá objednávací kód tovaru pri položkách?",
  running: false,
  created_at: T1,
  updated_at: T2,
};

const CONSULTATION = {
  consultation: {
    id: "konzultacia-1",
    intro: "Pri návrhu doručovania do NEX Genesis som narazil na dve veci, o ktorých musíš rozhodnúť ty.",
    round: 1,
    round_max: 5,
    decisions: [
      {
        key: "opakovanie",
        question: "Ako dlho má NEX Inbox skúšať doručiť faktúru, keď zdieľaný priečinok NEX Genesis neodpovedá?",
        explanation:
          "Spojenie medzi Komárnom a Tonkovcami ide cez internet a krátke výpadky sú bežné. Faktúra môže čakať, kým sa spojenie vráti, alebo po čase skončiť v manuálnom rade.",
        origin: "objav",
        technical_detail: "GenesisDeliverer: backoff 30 s → 15 min, strop pokusov podľa DELIVERY_GIVE_UP_HOURS.",
        options: [
          { id: "a", label: "Skúšať 24 hodín, potom manuálny rad", recommended: true, detail: "Obsluha dostane jeden e-mail." },
          { id: "b", label: "Skúšať bez obmedzenia", detail: "Faktúra nikdy neskončí v manuálnom rade." },
          { id: "c", label: "Skúšať 1 hodinu", detail: "Pri dlhšom výpadku pribudne práca obsluhe." },
        ],
        allow_free_text: true,
      },
      {
        key: "oznamenie",
        question: "Komu poslať e-mail o výpadku doručovania?",
        origin: "dosledok",
        origin_of: "opakovanie",
        options: [
          { id: "a", label: "Obsluhe aj správcovi systému", recommended: true },
          { id: "b", label: "Len správcovi systému" },
        ],
      },
    ],
  },
};

const MESSAGES: S["PipelineMessageRead"][] = [
  {
    id: "33333333-3333-4333-8333-000000000001",
    version_id: VERSION.id,
    seq: 1,
    stage: "navrh",
    kind: "kickoff",
    author: "manazer",
    recipient: "ai_agent",
    status: "done",
    content: "Začni návrh verzie 1.8.0 podľa zadania.",
    payload: null,
    created_at: T1,
  },
  {
    id: "33333333-3333-4333-8333-000000000002",
    version_id: VERSION.id,
    seq: 2,
    stage: "navrh",
    kind: "consultation",
    author: "ai_agent",
    recipient: "manazer",
    status: "open",
    content:
      "Návrh doručovania je pripravený. Pred pokračovaním potrebujem tvoje rozhodnutie o dvoch veciach, ktoré menia správanie aplikácie u zákazníka.",
    payload: CONSULTATION,
    created_at: T2,
  },
];

const PIPELINE: S["PipelineBoardRead"] = {
  state: {
    id: "44444444-4444-4444-8444-444444444444",
    version_id: VERSION.id,
    current_stage: "navrh",
    current_actor: "ai_agent",
    status: "awaiting_manazer",
    block_reason: "decision_needed",
    flow_type: "new_version",
    is_regate: false,
    iteration: 1,
    mode: null,
    next_action: "Rozhodni o dvoch otázkach k doručovaniu do NEX Genesis",
    pause_reason: null,
    resume_after_framework_fix: false,
    working_since: null,
    created_at: T1,
    updated_at: T2,
  },
  available_actions: ["decide", "pozastavit"],
  all_tasks_done: false,
  build_open_findings: 0,
  spec_approved: false,
  verified: false,
  verified_provenance: "",
  decision_context: {
    oznamenie: {
      origin_of_question: "Ako dlho má NEX Inbox skúšať doručiť faktúru?",
      origin_of_choice: null,
      reopens_closed: 0,
    },
  },
  consultation_direction: { blocking_by_round: [2], closed: 3, open: 2, last_round: null },
  current_task: { number: 12, title: "Doručovateľ faktúr do zdieľaného priečinka NEX Genesis na pozadí" },
  recent_messages: MESSAGES,
  agent_sessions: [{ role: "ai_agent", status: "idle" }],
  vizual_checklists: [],
  vizual_url: null,
  database_schema: null,
  dedo_proposal: null,
};

const STATE = PIPELINE.state!;

/**
 * The same build in its other stages — each changes what the control centre and the visual stage show (other bars, the live
 * preview, the composer or the Poradca bar) — and a deploy running on the UAT and PROD pages. Measured as their own tests on
 * the pages they change.
 */
export const BUILD_STAGES: { name: string; pages: string[]; overrides: Override[] }[] = [
  {
    name: "in the visual stage",
    // DEV-120: the Vizuál page is gone — the live preview opens from the Riadiace centrum (ZivyNahladBar).
    pages: ["/riadiace-centrum"],
    overrides: [
      // DEV-127: the preview of the visual stage is measured dead — the longest form of the strip (reason + button).
      override("get", "/api/v1/pipeline/{version_id}/vizual-preview", {
        needed: true,
        state: "stopped",
        url: null,
        reason:
          "Error: Živý náhľad potrebuje texty vydaní, ale priečinok /docs/specs/versions neexistuje. Náhľad sa stavia z koreňa projektu.",
      }),
      override("get", "/api/v1/pipeline/{version_id}", {
        ...PIPELINE,
        state: {
          ...STATE,
          current_stage: "vizual",
          block_reason: null,
          next_action: "Prezri si živý náhľad aplikácie a napíš, čo zmeniť, alebo vizuál schváľ",
        },
        available_actions: ["schvalit", "pozastavit"],
        recent_messages: MESSAGES.slice(0, 1),
        vizual_url: "about:blank",
        vizual_checklists: [
          {
            round: "1",
            seq: 3,
            not_verifiable: ["Odoslanie e-mailu obsluhe pri výpadku doručovania"],
            items: [
              {
                screen: "Prehľad",
                action: "Otvor Prehľad a pozri dlaždicu Čaká na doručenie do NEX Genesis",
                expected: "Dlaždica ukazuje počet faktúr, ktoré čakajú, a čas posledného pokusu",
                url: null,
              },
            ],
          },
        ],
      }),
    ],
  },
  {
    // DEV-115: a deploy runs — every row says its step and time, and its buttons are greyed out until it ends.
    name: "while a deploy runs",
    pages: ["/uat", "/prod"],
    overrides: [
      override("get", "/api/v1/customers/{customer_id}/deploy-progress", {
        bezi: true,
        krok: "zálohujem databázu — bežiaca inštalácia sa zatiaľ nemení",
        krok_trva_sekund: 42,
        trva_sekund: 312,
      }),
    ],
  },
  {
    name: "while the agent works",
    pages: ["/riadiace-centrum"],
    overrides: [
      override("get", "/api/v1/pipeline/{version_id}", {
        ...PIPELINE,
        state: {
          ...STATE,
          current_stage: "programovanie",
          status: "agent_working",
          block_reason: null,
          working_since: T2,
          next_action: "Programujem úlohu 12 — doručovateľ faktúr do zdieľaného priečinka NEX Genesis na pozadí",
        },
        available_actions: ["pozastavit"],
        agent_sessions: [{ role: "ai_agent", status: "active" }],
        recent_messages: MESSAGES.slice(0, 1),
      }),
    ],
  },
  {
    name: "stopped on a fault of the cockpit",
    pages: ["/riadiace-centrum"],
    overrides: [
      override("get", "/api/v1/pipeline/{version_id}", {
        ...PIPELINE,
        state: {
          ...STATE,
          current_stage: "programovanie",
          status: "blocked",
          block_reason: "framework_issue",
          next_action: "Čakám na opravu kokpitu — náš technický tím o tom vie",
        },
        available_actions: [],
        recent_messages: [
          ...MESSAGES.slice(0, 1),
          {
            ...MESSAGES[1]!,
            seq: 3,
            stage: "programovanie",
            kind: "question",
            status: "open",
            content:
              "Kontrola zostavenia sa nedá spustiť: kontajner skúšok nemá prístup k databáze. Toto nie je chyba aplikácie, ale prostredia kokpitu.",
            payload: { framework_issue: true, technical_detail: "docker: network nex-inbox-test not found" },
          },
        ],
      }),
    ],
  },
  {
    name: "when the version is finished",
    pages: ["/riadiace-centrum"],
    overrides: [
      override("get", "/api/v1/pipeline/{version_id}", {
        ...PIPELINE,
        state: { ...STATE, current_stage: "done", status: "done", block_reason: null, next_action: "Verzia je hotová" },
        available_actions: [],
        all_tasks_done: true,
        verified: true,
        verified_provenance: "Verifikácia prešla 11. 10. 2026 o 18:40 na stave kódu af97f37",
        recent_messages: MESSAGES.slice(0, 1),
      }),
    ],
  },
];

const TASK_PLAN: TaskPlanResponse = {
  epic_count: 2,
  feat_count: 2,
  task_count: 3,
  billing_default: "request",
  plan: [
    {
      id: "e1",
      number: 1,
      title: "Doručovanie faktúr do NEX Genesis na pozadí s opakovaním pri výpadku spojenia",
      status: "in_progress",
      plain_description: "Faktúra počká, kým sa spojenie vráti, a obsluha dostane jeden e-mail.",
      spend: null,
      feats: [
        {
          id: "f1",
          number: 1,
          title: "Doručovateľ s časovým stropom každého dotyku zdieľania",
          status: "in_progress",
          description: "",
          plain_description: "Aplikácia nezamrzne, keď zdieľaný priečinok mlčí.",
          spend: null,
          tasks: [
            {
              id: "t1",
              number: 12,
              title: "GenesisDeliverer — jeden pokus o doručenie faktúry z archívu",
              task_type: "backend",
              status: "in_progress",
              priority: "high",
              checklist_type: null,
              description: "",
              plain_description: "Jeden pokus o doručenie, výsledok sa zapíše k faktúre.",
              spend: null,
            },
            {
              id: "t2",
              number: 13,
              title: "Obrazovka Prehľad ukáže čakajúce doručenia",
              task_type: "frontend",
              status: "todo",
              priority: "medium",
              checklist_type: null,
              description: "",
              plain_description: "Obsluha vidí, ktoré faktúry čakajú na doručenie.",
              spend: null,
              billing_category: "gift",
            },
          ],
        },
      ],
    },
    {
      id: "e2",
      number: 2,
      title: "Poznámky k vydaniu",
      status: "planned",
      plain_description: "Zákazník vidí, čo sa vo verzii zmenilo.",
      spend: null,
      feats: [
        {
          id: "f2",
          number: 2,
          title: "Záložka Aktualizácie",
          status: "todo",
          description: "",
          plain_description: "",
          spend: null,
          tasks: [
            {
              id: "t3",
              number: 14,
              title: "Poznámky k vydaniu 1.8.0",
              task_type: "docs",
              status: "todo",
              priority: "low",
              checklist_type: null,
              description: "",
              plain_description: "",
              spend: null,
            },
          ],
        },
      ],
    },
  ],
};

const CUSTOMER: S["CustomerRead"] = {
  id: "55555555-5555-4555-8555-555555555555",
  project_id: PROJECT.id,
  name: "MÁGERSTAV spol. s r.o. — stavebniny a železiarstvo Komárno",
  slug: "magerstav",
  subdomain: "mager",
  has_secret: true,
  integrations: null,
  notes: "Faktúry doručuje do NEX Genesis na srv-mager-w2012 v Tonkovciach.",
  settings: { BOOTSTRAP_ADMIN_EMAIL: "spravca.faktur@magerstav.example.sk" },
  secret_settings_set: ["BOOTSTRAP_ADMIN_PASSWORD"],
  created_at: T0,
  updated_at: T1,
};

/** Added a week ago, nothing installed yet — the row that offers to adopt a hand-written installation. */
const NEW_CUSTOMER: S["CustomerRead"] = {
  ...CUSTOMER,
  id: "55555555-5555-4555-8555-555555555556",
  name: "Stavebniny Novák",
  slug: "novak",
  subdomain: "novak",
  has_secret: false,
  notes: null,
  settings: {},
  secret_settings_set: [],
};

const DEPLOY_MATRIX: S["DeployMatrix"] = {
  project_slug: PROJECT.slug,
  auth_mode: "token",
  can_accept: true,
  can_deploy: true,
  can_deploy_prod: true,
  verified_versions: ["1.7.1"],
  deployability: {
    cause: "ok",
    can_reverify: false,
    version_id: RELEASED.id,
    version_number: RELEASED.version_number,
  },
  rows: [
    {
      customer_id: CUSTOMER.id,
      customer_name: CUSTOMER.name,
      customer_slug: CUSTOMER.slug,
      subdomain: CUSTOMER.subdomain,
      accepted_versions: ["1.7.1"],
      uat_version: "1.7.1",
      uat_url: "https://uat-mager-inbox.isnex.eu",
      uat_last_deploy_at: "2026-10-11T13:30:00Z",
      uat_last_deploy_detail:
        "Nasadené 1.7.1. Kontrola zdravia pred aj po nasadení: database ok, imap ok, smb ok. Vstupenka z NEX Managera prešla.",
      uat_last_deploy_warnings: [],
      uat_last_attempt_failed: false,
      prod_version: "1.7.1",
      prod_url: "https://magerstav-inbox.icc.sk",
      prod_last_deploy_at: "2026-10-11T19:53:00Z",
      prod_last_deploy_detail: "Nasadené na MAGER; verejná adresa odpovedá.",
      // DEV-129: a long warning the row must wrap, not push the page sideways.
      prod_last_deploy_warnings: [
        "Aplikácia je nasadená a beží, ale spustenie jedným kliknutím (tlačidlo „Spustiť“) zatiaľ nefunguje: nie je nasadený NEX Manager tohto zákazníka.",
      ],
      prod_last_attempt_failed: false,
    },
    {
      customer_id: NEW_CUSTOMER.id,
      customer_name: NEW_CUSTOMER.name,
      customer_slug: NEW_CUSTOMER.slug,
      subdomain: NEW_CUSTOMER.subdomain,
      accepted_versions: [],
      uat_version: null,
      uat_url: null,
      uat_last_deploy_at: null,
      uat_last_deploy_detail: null,
      uat_last_deploy_warnings: [],
      uat_last_attempt_failed: false,
      prod_version: null,
      prod_url: null,
      prod_last_deploy_at: null,
      prod_last_deploy_detail: null,
      prod_last_deploy_warnings: [],
      prod_last_attempt_failed: false,
    },
  ],
};

const ZERO_TOTALS: S["CostTotalsRead"] = {
  agent_cost_complete: true,
  agent_cost_external: 0,
  agent_cost_measured: 41.37,
  agent_cost_total: 41.37,
  cache_read_tokens: 18_400_000,
  cache_write_tokens: 1_250_000,
  human_cost_external: 0,
  human_cost_measured: 312.5,
  human_cost_total: 312.5,
  human_minutes_external: 0,
  human_minutes_measured: 625,
  human_minutes_total: 625,
  input_tokens: 2_340_000,
  input_tokens_external: 0,
  input_tokens_measured: 2_340_000,
  output_tokens: 412_000,
  output_tokens_external: 0,
  output_tokens_measured: 412_000,
  turns: 184,
  turns_external: 0,
  turns_measured: 184,
};

const PRICE: S["PriceListRead"] = {
  model: "opus",
  valid_from: "2026-09-01",
  input_usd: 15,
  output_usd: 75,
  cache_read_usd: 1.5,
  cache_write_usd: 18.75,
  web_search_usd: 10,
  input_eur: 12.9,
  output_eur: 64.5,
  cache_read_eur: 1.29,
  cache_write_eur: 16.13,
  web_search_eur: 8.6,
  eur_usd: 1.163,
  rate_date: "2026-10-09",
  rate_source: "ECB",
  max_deviation: 0.002,
  observations: 1840,
};

const ROW: S["CostRowRead"] = {
  key: "programovanie",
  kind: "phase",
  active_seconds: 21_600,
  turns: 120,
  input_tokens: 1_500_000,
  output_tokens: 300_000,
  cache_read_tokens: 12_000_000,
  cache_write_tokens: 800_000,
  agent_cost: 28.4,
  human_cost: 210,
  human_minutes: 420,
  share_pct: 68.6,
  unpriced: [],
};

const COSTS: S["ProjectCostsRead"] = {
  project_id: PROJECT.id,
  slug: PROJECT.slug,
  currency: "EUR",
  coefficient_minutes_per_mtok: 0.25,
  wages: { manazer: 30, technik: 25 },
  wages_configured: true,
  manager: { interventions: 23, wait_seconds: 5400 },
  price_list: [PRICE],
  rows: [ROW, { ...ROW, key: "navrh", active_seconds: 7200, turns: 64, share_pct: 31.4, agent_cost: 12.97 }],
  totals: ZERO_TOTALS,
  usage: { duration_seconds: 28_800, input_tokens: 2_340_000, output_tokens: 412_000, messages: 368 },
  by_version: [
    {
      version_id: RELEASED.id,
      version_number: RELEASED.version_number,
      status: "released",
      total_time_seconds: 86_400,
      internal_idle_seconds: 3600,
      manager_wait_seconds: 5400,
      manager: { interventions: 23, wait_seconds: 5400 },
      price_list: [PRICE],
      rows: [ROW],
      totals: ZERO_TOTALS,
      usage: { duration_seconds: 28_800, input_tokens: 2_340_000, output_tokens: 412_000, messages: 368 },
    },
  ],
};

const SETTING: S["SystemSettingRead"] = {
  key: "github_org",
  label: "Organizácia na GitHube pre nové projekty",
  value: "rauschiccsk",
  value_type: "string",
  unit: "",
  is_default: false,
  description: "Kam kokpit zakladá repozitáre nových projektov.",
  updated_at: T1,
  updated_by: USER.id,
  updated_by_username: USER.username,
};

const DOCS: KnowledgeDoc[] = [
  { relative_path: "icc/CLEAN_CODE.md", filename: "CLEAN_CODE.md", category: "icc", size_bytes: 18_230 },
  {
    relative_path: "projects/nex-inbox/docs/ARCHITECTURE-dorucovanie-do-genesisu.md",
    filename: "ARCHITECTURE-dorucovanie-do-genesisu.md",
    category: "projects",
    size_bytes: 42_100,
  },
];

/** DEV-82 — the person's layout as the server keeps it: nothing chosen yet, with the bounds of every width. */
export const LAYOUT = {
  sidebar_width: null,
  sidebar_collapsed: false,
  plan_width: null,
  limits: {
    sidebar_width: { min: 180, max: 360, default: 224 },
    plan_width: { min: 280, max: 640, default: 320 },
  },
};

/** DEV-84 — the runs GitHub listed for NEX Inbox on 12.10.2026, in the shapes the repository tab draws. */
export const CI_RUNS: S["CiRunsRead"] = {
  project_name: PROJECT.name,
  repo: "rauschiccsk/nex-inbox",
  actions_url: "https://github.com/rauschiccsk/nex-inbox/actions",
  total_count: 193,
  fetched_at: T2,
  error: null,
  stale: false,
  refresh_seconds: 30,
  runs: [
    {
      id: 38305919265,
      number: 25,
      attempt: 1,
      workflow: "Release smoke gate",
      subject: "proof(red): nginx without the .mjs type (do not merge)",
      event: "workflow_dispatch",
      event_label: "spustené ručne",
      state: "failure",
      state_label: "Zlyhalo",
      branch: "ci/v180-disk-blackhole-red-mount-and-a-long-branch-name",
      sha: "577bd01aa2f6c1f0e7d1c9b8a7f6e5d4c3b2a190",
      actor: "rauschiccsk",
      created_at: T2,
      duration_seconds: 716,
      url: "https://github.com/rauschiccsk/nex-inbox/actions/runs/38305919265",
      failed_step: "smb-blackhole › Skutočné zdieľanie zmĺkne a vráti sa (deploy/ci/smb_blackhole_check.sh)",
      task: null,
      deployed: [],
    },
    {
      id: 38305915333,
      number: 24,
      attempt: 1,
      workflow: "CI",
      subject: "test(1.8.0): release gate — three features and SP-21…SP-27 (TASK #32.2.1)",
      event: "push",
      event_label: "odoslanie",
      state: "success",
      state_label: "Prešlo",
      branch: "main",
      sha: "1771419bb2f6c1f0e7d1c9b8a7f6e5d4c3b2a190",
      actor: "rauschiccsk",
      created_at: T1,
      duration_seconds: 784,
      url: "https://github.com/rauschiccsk/nex-inbox/actions/runs/38305915333",
      failed_step: null,
      task: {
        label: "32.2.1",
        task_id: "33333333-3333-4333-8333-333333333321",
        title: "Brána vydania — tri funkcie a SP-21…SP-27",
        version_id: VERSION.id,
        version_number: VERSION.version_number,
      },
      deployed: [
        { environment: "prod", customer: "MÁGERSTAV spol. s r.o.", version: "1.7.1" },
        { environment: "uat", customer: "ICC s.r.o.", version: "1.7.1" },
      ],
    },
    {
      id: 38302046414,
      number: 168,
      attempt: 1,
      workflow: "CI",
      subject: "feat(1.8.0): disk check of the Genesis share (TASK #33.1.2)",
      event: "push",
      event_label: "odoslanie",
      state: "running",
      state_label: "Beží",
      branch: "main",
      sha: "2fc9065bb2f6c1f0e7d1c9b8a7f6e5d4c3b2a190",
      actor: "rauschiccsk",
      created_at: T2,
      duration_seconds: 95,
      url: "https://github.com/rauschiccsk/nex-inbox/actions/runs/38302046414",
      failed_step: null,
      task: null,
      deployed: [],
    },
  ],
};

export function registerFixtures(): void {
  answer("get", "/api/v1/auth/me", USER);
  answer("get", "/api/v1/user-layout", LAYOUT);
  answer("put", "/api/v1/user-layout", LAYOUT);
  // DEV-89 — an open note with a long first line, and one resolved into a request of the selected project.
  answer("get", "/api/v1/notes", [
    {
      id: "aaaaaaaa-0000-4000-8000-000000000089",
      body: "Zavolať Tiborovi kvôli faktúram MÁGERSTAV, ktoré čakajú na doručenie do NEX Genesis od piatku\nA spýtať sa na dobropisy.",
      title: "Zavolať Tiborovi kvôli faktúram MÁGERSTAV, ktoré čakajú na doručenie do NEX Genesis od piatku",
      revision: 3,
      created_at: T1,
      updated_at: T2,
      resolved_at: null,
      backlog: null,
    },
    {
      id: "aaaaaaaa-0000-4000-8000-000000000090",
      body: "Export do CSV pri zozname faktúr\nStĺpce ako v Exceli.",
      title: "Export do CSV pri zozname faktúr",
      revision: 1,
      created_at: T1,
      updated_at: T1,
      resolved_at: T2,
      backlog: {
        id: "aaaaaaaa-0000-4000-8000-000000000091",
        project_id: PROJECT.id,
        project_slug: PROJECT.slug,
        project_name: PROJECT.name,
        number: 12,
      },
    },
  ]);
  answerUntyped("get", "/api/v1/version", { version: "4.43.26" });
  answer("get", "/api/v1/projects", { items: [PROJECT], total: 1, skip: 0, limit: 50 });
  answer("get", "/api/v1/projects/{project_id}", PROJECT);
  answer("get", "/api/v1/projects/{project_id}/versions", [VERSION, RELEASED]);
  answer("get", "/api/v1/projects/{project_id}/assignments", [
    {
      assigned_by_person: "Mária Manažérová",
      from_person: null,
      to_person: "Mária Manažérová",
      note: "Zakladateľ projektu",
      created_at: T0,
    },
  ]);
  answer("get", "/api/v1/projects/{project_id}/dedo-proposal", null);
  answerUntyped<GitStatus>("get", "/api/v1/projects/{project_id}/git-status", {
    clean: false,
    dirty_count: 2,
    truncated: false,
    files: [
      { code: " M", path: "backend/apps/smb_writer/deliverer_with_a_rather_long_module_name.py" },
      { code: "??", path: "docs/specs/versions/v1.8.0/RELEASE_NOTES.md" },
    ],
  });
  answerUntyped<NexsharedStatus>("get", "/api/v1/projects/{project_id}/nexshared-status", {
    current: "v0.19.0",
    latest: "v0.20.0",
    behind: 1,
    up_to_date: false,
    changelog: [{ version: "v0.20.0", body: "[vzhľad] Kostra aplikácie sa prispôsobí mobilu a tabletu." }],
  });
  answer("get", "/api/v1/projects/ports/suggest-block", { base: 9300, block_size: 3, warnings: [] });
  answer("get", "/api/v1/projects/create-presets", {
    enable_cicd: true,
    enable_branch_protection: false,
    branch_protection_available: false,
    branch_protection_note: "Ochrana hlavnej vetvy potrebuje platený plán GitHubu pre súkromné repozitáre.",
    full_smoke: true,
    custom_development_enabled: false,
    private_network: false,
    // DEV-160: Web stays closed, with the reason under it, until the cockpit can build a second frontend.
    web_available: false,
    web_note:
      "Projekt s webom a administráciou kokpit ešte nevie postaviť ani nasadiť — učí sa to po krokoch a voľba Web " +
      "sa sprístupní, keď bude hotový posledný z nich.",
  });
  answerUntyped<AdoptableCandidate[]>("get", "/api/v1/projects/adoptable", [
    { slug: "nex-ledger", source_path: "/opt/projects/nex-ledger", name: "NEX Ledger" },
    { slug: "career-asistent-pre-uchadzacov-o-zamestnanie", source_path: "/opt/projects/career-asistent", name: null },
  ]);
  answer("get", "/api/v1/versions/{version_id}", VERSION);
  answer("get", "/api/v1/versions/{version_id}/nastavenia", { version_number_lock_reason: null });
  answerUntyped<TaskPlanResponse>("get", "/api/v1/versions/{version_id}/task-plan", TASK_PLAN);
  answer("get", "/api/v1/versions/{version_id}/zadanie", {
    content:
      "# Zadanie NEX Inbox 1.8.0\n\nFaktúry sa doručujú do zdieľaného priečinka NEX Genesis na pozadí. Pri výpadku spojenia faktúra čaká a obsluha dostane jeden e-mail.\n",
  });
  answer("get", "/api/v1/pipeline/{version_id}", PIPELINE);
  answer("get", "/api/v1/pipeline/{version_id}/ci", {
    bezi: false,
    stav: "success",
    detail: "Kontrola na GitHube prešla (lint, skúšky, stavba).",
    sha: "af97f3721c65713dfbbfdb71cc3b0a81375dd80d",
    url: "https://github.com/rauschiccsk/nex-inbox/actions/runs/38071200386",
  });
  answer("get", "/api/v1/pipeline/{version_id}/files", {
    max_bytes: 10_485_760,
    max_label: "10 MB",
    files: [
      {
        path: "private/vzorova-faktura-kinekus-s-prilozenym-xml-baris.pdf",
        size_bytes: 184_320,
        size_label: "180 kB",
        modified_at: T1,
        uploaded_at: T1,
        uploaded_by: USER.username,
      },
    ],
  });
  answer("get", "/api/v1/backlog", {
    items: [
      {
        id: "66666666-6666-4666-8666-666666666661",
        number: 7,
        project_id: PROJECT.id,
        version_id: null,
        title: "Čítať čiarový kód EAN z obrázka faktúry, keď ho dodávateľ nemá v texte PDF",
        description: "REBIOP má skutočný GTIN len v čiarach čiarového kódu.",
        priority: "medium",
        status: "open",
        realized_at: null,
        rejection_reason: null,
        plane_identifier: null,
        plane_url: null,
        created_at: T0,
        updated_at: T1,
      },
      {
        // DEV-121: a requirement with its ticket — the link to Plane must be touchable on a phone.
        id: "66666666-6666-4666-8666-666666666662",
        number: 8,
        project_id: PROJECT.id,
        version_id: VERSION.id,
        title: "Import faktúr z ISDOC",
        description: null,
        priority: "high",
        status: "included",
        realized_at: null,
        rejection_reason: null,
        plane_identifier: "DEV-130",
        plane_url: "http://plane.test/icc/projects/p1/issues/i130",
        created_at: T0,
        updated_at: T1,
      },
    ],
    total: 2,
    skip: 0,
    limit: 50,
  });
  // DEV-121: the draft „Založiť tiket" opens with — the layout check opens the panel too.
  answer("get", "/api/v1/backlog/{item_id}/plane-ticket", {
    title: "Čítať čiarový kód EAN z obrázka faktúry, keď ho dodávateľ nemá v texte PDF",
    description: "REBIOP má skutočný GTIN len v čiarach čiarového kódu.",
    ticket_type: "Task",
    responsible_options: ["Andros Dedo", "Zoltán"],
    responsible: "Zoltán",
    plane_identifier: null,
    plane_url: null,
  });
  // DEV-120: the „Plane tikety" tab — one ticket the Zásobník founded, one from a Dedo.
  // DEV-122: an open ticket goes to the Zásobník or starts a fast fix — the layout check opens the fast fix panel.
  answer("get", "/api/v1/projects/{slug}/plane-tickets", {
    project_id: PROJECT.id,
    project_name: PROJECT.name,
    module: PROJECT.name,
    tickets: [
      {
        issue_id: "i130",
        identifier: "DEV-130",
        title: "Čítať čiarový kód EAN z obrázka faktúry, keď ho dodávateľ nemá v texte PDF",
        state: "In Progress",
        closed: false,
        ticket_type: "Task",
        work_kind: "change",
        work_kind_label: "Požiadavka (CHR)",
        warning: null,
        doer: "Zoltán",
        updated_at: T1,
        url: "http://plane.test/icc/projects/p1/issues/i130",
        requirement: "REQ-7",
        requirement_version: "v1.9.0",
        text: "REBIOP má skutočný GTIN len v čiarach čiarového kódu.",
        fix_version: null,
      },
      {
        issue_id: "i114",
        identifier: "DEV-114",
        title: "NEX Inbox: faktúra FLORASERVIS sa nespracuje z ISDOC — číslo faktúry je v ISDOC len vo variabilnom symbole",
        state: "In Progress",
        closed: false,
        ticket_type: "Bug",
        work_kind: "fix",
        work_kind_label: "Oprava (BUG)",
        warning: null,
        doer: "Zoltán Rausch",
        updated_at: T0,
        url: "http://plane.test/icc/projects/p1/issues/i114",
        requirement: null,
        requirement_version: null,
        text: "Číslo faktúry je v ISDOC len vo variabilnom symbole.\n\nOver aj dobropis.",
        fix_version: null,
      },
    ],
  });
  answer("get", "/api/v1/projects/{slug}/customers", [CUSTOMER, NEW_CUSTOMER]);
  answer("get", "/api/v1/projects/{slug}/customer-settings", {
    settings: [
      { key: "APP_PUBLIC_URL", kind: "public_url", required: false, label: "APP_PUBLIC_URL" },
      {
        key: "BOOTSTRAP_ADMIN_EMAIL",
        kind: "customer",
        required: true,
        label: "E-mail prvého správcu — týmto e-mailom sa prvý raz prihlásiš",
      },
      { key: "BOOTSTRAP_ADMIN_PASSWORD", kind: "customer_secret", required: true, label: "Heslo prvého správcu" },
      { key: "INTAKE_IMAP_HOST", kind: "customer", required: false, label: "Server schránky, z ktorej sa čítajú faktúry" },
      { key: "INTAKE_IMAP_PASSWORD", kind: "customer_secret", required: false, label: "Heslo k schránke s faktúrami" },
    ],
    problems: [],
    legacy_admin: false,
  });
  answer("get", "/api/v1/projects/{slug}/deploy-matrix", DEPLOY_MATRIX);
  // DEV-115: no deploy runs — the pages as they are most of the time; a running one is a stage of its own (BUILD_STAGES).
  answer("get", "/api/v1/customers/{customer_id}/deploy-progress", {
    bezi: false,
    krok: null,
    krok_trva_sekund: 0,
    trva_sekund: 0,
  });
  // DEV-84: the repository tab — a red proof with its failed step, a run with its task and where it runs, one running.
  answer("get", "/api/v1/projects/{slug}/ci-runs", CI_RUNS);
  // DEV-158: NEX Inbox as on 20.10.2026 — taken over, no runner of its own, every job on GitHub's machines.
  answer("get", "/api/v1/projects/{slug}/ci-runner", {
    label: "andros-ubuntu-nex-inbox",
    repo: "rauschiccsk/nex-inbox",
    state: "none",
    github_status: null,
    starting: false,
    healthy: false,
    summary: "Projekt nemá vlastný vykonávač kontrol na ANDROSe.",
    action: "Spustiť vykonávač kontrol",
    note: "Vykonávač kontrol sa do 120 s neprihlásil na GitHube — prvá kontrola počká, kým sa prihlási.",
    jobs_elsewhere: [
      ...["lint", "drift-check", "test", "build", "chrome109", "smb-blackhole"].map((job) => ({
        workflow: "ci.yml",
        job,
        runs_on: "ubuntu-latest",
      })),
      { workflow: "release-smoke.yml", job: "smoke", runs_on: "ubuntu-latest" },
      { workflow: "release-smoke.yml", job: "gate", runs_on: "ubuntu-latest" },
    ],
    unreadable_workflows: [],
  });
  // DEV-86: NS Code — a few folders and files of NEX Inbox, one file with a long line, a search with hits.
  answer("get", "/api/v1/projects/{slug}/code/files", {
    head: "d626301458a1b2c3d4e5f60718293a4b5c6d7e8f",
    files: [
      ".env.example",
      ".gitignore",
      "README.md",
      "backend/apps/xml_exporter/ubl_builder.py",
      "backend/main.py",
      "docs/specs/backend/BEHAVIOR.md",
      "frontend/src/pages/InvoiceDetailPage.tsx",
      "package.json",
    ],
    // DEV-87 — the agent is changing main.py and has added a new page.
    changes: { "backend/main.py": "M", "frontend/src/pages/InvoiceDetailPage.tsx": "U" },
    stamps: {
      ".env.example": "52-1",
      ".gitignore": "40-1",
      "README.md": "3958-1",
      "backend/apps/xml_exporter/ubl_builder.py": "18000-1",
      "backend/main.py": "412-2",
      "docs/specs/backend/BEHAVIOR.md": "64000-1",
      "frontend/src/pages/InvoiceDetailPage.tsx": "9000-2",
      "package.json": "1200-1",
    },
    refresh_seconds: 10,
  });
  answer("get", "/api/v1/projects/{slug}/code/file", {
    path: "backend/main.py",
    size: 412,
    text:
      '"""NEX Inbox — the backend of the invoice inbox."""\n\nfrom fastapi import FastAPI\n\napp = FastAPI(title="NEX Inbox")\n\n\n' +
      "@app.get(\"/api/v1/health\")\ndef health() -> dict[str, str]:\n    return {\"status\": \"ok\", \"database\": \"ok\", \"genesis_share\": \"reachable over SMB from srv-mager-w2012\"}\n",
    binary: false,
    too_big: false,
    max_bytes: 1000000,
    stamp: "412-2",
    last_commit: {
      sha: "1771419bb2f6c1f0e7d1c9b8a7f6e5d4c3b2a190",
      at: T1,
      subject: "test(1.8.0): release gate — three features and SP-21…SP-27 (TASK #32.2.1)",
      task: {
        label: "32.2.1",
        task_id: "33333333-3333-4333-8333-333333333321",
        title: "Brána vydania — tri funkcie a SP-21…SP-27",
        version_id: VERSION.id,
        version_number: VERSION.version_number,
      },
    },
  });
  answer("get", "/api/v1/projects/{slug}/code/original", {
    path: "backend/main.py",
    against: "head",
    rev: "1771419bb2f6c1f0e7d1c9b8a7f6e5d4c3b2a190",
    existed: true,
    text: '"""NEX Inbox — the backend of the invoice inbox."""\n\nfrom fastapi import FastAPI\n\napp = FastAPI()\n',
    binary: false,
    too_big: false,
  });
  answer("get", "/api/v1/projects/{slug}/code/search", {
    query: "SellersItemIdentification",
    hits: [
      { path: "backend/apps/xml_exporter/ubl_builder.py", line: 214, text: "    seller_id = SubElement(item, cac('SellersItemIdentification'))" },
      { path: "docs/specs/backend/BEHAVIOR.md", line: 88, text: "Kód položky dodávateľa ide do SellersItemIdentification (BT-155)." },
    ],
    truncated: false,
  });
  // DEV-85: the main branch is red — the repository tab carries the red mark.
  answer("get", "/api/v1/projects/{slug}/ci-main", {
    branch: "main",
    failing: [
      {
        workflow: "CI",
        number: 168,
        subject: "feat(1.8.0): disk check of the Genesis share (TASK #33.1.2)",
        failed_step: "lint › Frontend type-check + lint",
        url: "https://github.com/rauschiccsk/nex-inbox/actions/runs/38302046414",
      },
    ],
    refresh_seconds: 60,
  });
  // DEV-90: what to try in UAT — a guide with two changes, so the UAT tab is measured with it.
  answer("get", "/api/v1/projects/{slug}/uat-guide", {
    version_number: "1.7.1",
    source: "guide",
    sections: [
      {
        title: "Detail faktúry sa otvorí aj v staršom prehliadači na serveri",
        screen: "Faktúry → detail faktúry",
        data: "faktúra EMOS SK s.r.o. 9084643319",
        steps: ["Otvor NEX Inbox z NEX Genesis.", "V zozname faktúr klikni na faktúru EMOS.", "Prejdi na druhú stranu PDF."],
        expected: ["Detail sa otvorí, PDF je vidno a dá sa listovať."],
      },
      {
        title: "Objednávkový kód z ISDOC",
        screen: "Faktúry → detail faktúry → Položky",
        steps: ["Otvor faktúru od RMJ, s.r.o. prijatú ako ISDOC."],
        expected: ["Pri každej položke je objednávkový kód (napr. S093276315)."],
      },
    ],
    nothing_to_test: null,
    changes: [],
    logins: [CUSTOMER.id, NEW_CUSTOMER.id].map((id) => ({ customer_id: id, login: "operator@priklad.sk" })),
  });
  // DEV-91: what was tried — one change ticked off, so the guard measures the guide with its controls.
  answer("get", "/api/v1/customers/{customer_id}/uat-checks", {
    version_number: "1.7.1",
    items: ["Detail faktúry sa otvorí aj v staršom prehliadači na serveri", "Objednávkový kód z ISDOC"],
    checks: [
      {
        item: "Detail faktúry sa otvorí aj v staršom prehliadači na serveri",
        status: "tested",
        note: null,
        checked_by: "Mária Manažérová",
        updated_at: T2,
      },
    ],
    tested: 1,
    total: 2,
    untested: ["Objednávkový kód z ISDOC"],
    broken: [],
  });
  answerUntyped<AdoptionPreview>("get", "/api/v1/customers/{customer_id}/adoption-preview", {
    instance_dir: "/opt/uat/novak/nex-inbox",
    exists: true,
    already_ours: false,
    set_aside: [
      ["docker-compose.yml", "docker-compose.yml.pred-prevzatim"],
      [".env", ".env.pred-prevzatim"],
    ],
    untouched: ["data/", "originals/", "backups/nex_inbox-2026-10-01.dump"],
    running_containers: ["novak-inbox-backend", "novak-inbox-frontend", "novak-inbox-postgres"],
    carried_over: ["POSTGRES_PASSWORD", "IMAP_PASSWORD", "MANAGER_LAUNCH_SIGNING_KEY"],
    blocking: [],
    confirmation_phrase: "prevziať novak",
    can_adopt: true,
  });
  answer("get", "/api/v1/projects/{slug}/external-costs", [
    {
      id: "77777777-7777-4777-8777-777777777771",
      project_id: PROJECT.id,
      version_id: RELEASED.id,
      model: "opus",
      description: "Previerka návrhu doručovania mimo kokpitu (sedenie Deda)",
      input_tokens: 120_000,
      output_tokens: 18_000,
      occurred_on: "2026-10-10",
      created_by: USER.id,
      created_at: T1,
      updated_at: T1,
    },
  ]);
  answer("get", "/api/v1/projects/{slug}/metrics", COSTS);
  answer("get", "/api/v1/credentials", [
    {
      id: "88888888-8888-4888-8888-888888888881",
      title: "Prístup NEX Inboxu k poštovej schránke faktúr MÁGERSTAV",
      file_path: "mager/nex-inbox/imap.md",
      created_at: T0,
      updated_at: T1,
    },
  ]);
  answerUntyped("get", "/api/v1/knowledge/categories", { categories: ["icc", "projects", "infrastructure"] });
  answerUntyped("get", "/api/v1/knowledge/documents", { documents: DOCS, count: DOCS.length });
  answer("get", "/api/v1/rag/index-status", {
    indexed: 412,
    on_disk: 415,
    missing: 3,
    orphaned: 0,
    out_of_sync: 1,
    stale: 0,
    last_indexed_at: T2,
    sample: ["projects/nex-inbox/docs/ARCHITECTURE-dorucovanie-do-genesisu.md"],
  });
  answer("get", "/api/v1/project-specs/list", {
    count: 1,
    documents: [
      { category: "backend", filename: "BEHAVIOR.md", relative_path: "backend/BEHAVIOR.md", size_bytes: 96_000, is_directory: false },
    ],
  });
  answer("get", "/api/v1/poradca/status", {
    ready: true,
    running: 0,
    max_concurrent: 2,
    problems: [],
    attachment_max_bytes: 10_485_760,
    attachments_max_count: 5,
    attachments_max_total_bytes: 26_214_400,
    question_max_chars: 20_000,
    attachment_types: ["pdf", "png", "jpg", "txt", "md"],
  });
  answer("get", "/api/v1/poradca/projects/{slug}/context", {
    project_id: PROJECT.id,
    project_name: PROJECT.name,
    project_slug: PROJECT.slug,
    versions: [
      { id: VERSION.id, version_number: VERSION.version_number, stage: "navrh", status: "awaiting_manazer", instruction_open: true },
      {
        id: RELEASED.id,
        version_number: RELEASED.version_number,
        stage: "done",
        status: "done",
        instruction_open: false,
        instruction_closed_reason: "Verzia je hotová.",
      },
    ],
  });
  answer("get", "/api/v1/poradca/projects/{slug}/conversations", [PORADCA_CONVERSATION]);
  answer("get", "/api/v1/poradca/conversations/{conversation_id}", {
    ...PORADCA_CONVERSATION,
    messages: [
      {
        id: "99999999-9999-4999-8999-999999999992",
        author: "human",
        content: "Ktorú možnosť mám vybrať na kartách?",
        status: "done",
        created_at: T1,
      },
      {
        id: "99999999-9999-4999-8999-999999999993",
        author: "poradca",
        content:
          "Agent má v karte presný plán.\n\n" +
          'Na karte 2 vyber <volba karta="2">Zhasnúť hneď, keď sa doručovanie obnoví</volba>.\n' +
          'Na karte 1 by som zvolil <volba karta="1">posielať po kúskoch</volba>.\n\n' +
          "Agentovi potom odpovedz <volba>Áno, súhlasím</volba>.",
        status: "done",
        duration_seconds: 41,
        created_at: T1,
        finished_at: T2,
        choices: [
          { card: 2, label: "Zhasnúť hneď, keď sa doručovanie obnoví", status: "on_card", question: "Kedy zhasne upozornenie?" },
          { card: 1, label: "posielať po kúskoch", status: "not_on_card", question: "Čo s veľkou faktúrou?" },
          { card: null, label: "Áno, súhlasím", status: "answer", question: null },
        ],
      },
      // DEV-119: an answer proposing a ticket of the cockpit — the layout check opens its preview.
      {
        id: "99999999-9999-4999-8999-999999999994",
        author: "poradca",
        content:
          "Toto je chyba kokpitu.\n<tiket-nsv>\nTlačidlo Nasadiť pri UAT nič nerobí\n\nPo kliknutí sa neobjaví hláška.\n</tiket-nsv>",
        status: "done",
        duration_seconds: 12,
        created_at: T2,
        finished_at: T2,
        ticket_proposal: { kind: "nsv", title: "Tlačidlo Nasadiť pri UAT nič nerobí" },
      },
    ],
  });
  answer("get", "/api/v1/poradca/messages/{message_id}/plane-ticket", {
    kind: "nsv",
    title: "Tlačidlo Nasadiť pri UAT nič nerobí",
    description: "Po kliknutí sa neobjaví hláška.",
    ticket_type: "Bug",
    work_kind: null,
    way: null,
    responsible_options: ["Andros Dedo"],
    responsible: "Andros Dedo",
    context: [
      `Projekt: ${PROJECT.name}`,
      "Verzia: v1.9.0",
      "Stavba: fáza vizual, stav awaiting_manazer",
      "Rozhovor Poradcu: https://studio.int.isnex.eu/poradca?c=99999999-9999-4999-8999-999999999991",
    ],
    similar: [
      {
        identifier: "DEV-40",
        title: "Tlačidlo Nasadiť pri UAT zašedne bez dôvodu, keď verzia ešte nie je overená",
        state: "Todo",
        url: "http://plane.test/icc/projects/p1/issues/i40",
      },
    ],
    plane_identifier: null,
    plane_url: null,
  });
  answer("get", "/api/v1/release-notes", [
    {
      version: "4.43.26",
      released_at: "2026-10-12",
      markdown:
        "## 4.43.26\n\nKokpit povie, keď sa aplikácia nedá otvoriť z NEX Managera — kontrola dverí podpisuje vstupenku ako NEX Manager.\n",
    },
  ]);
  answer("get", "/api/v1/system-settings", [SETTING]);
  answer("get", "/api/v1/system-settings/{key}", SETTING);
  answer("get", "/api/v1/user-agent-settings/models", [
    { id: "opus", last_run_at: T2, last_run_model: "claude-opus-5-5" },
    { id: "sonnet", last_run_at: null, last_run_model: null },
    { id: "haiku", last_run_at: null, last_run_model: null },
  ]);
  answer("get", "/api/v1/users", { items: [USER, COLLEAGUE], total: 2, skip: 0, limit: 50 });
  answer("get", "/api/v1/user-agent-settings", [
    { agent_role: "ai_agent", model: "opus", effort: "high", helper_model: "sonnet" },
    { agent_role: "auditor", model: "opus", effort: "max", helper_model: null },
    { agent_role: "poradca", model: "sonnet", effort: "medium", helper_model: null },
  ]);
  answer("get", "/api/v1/user-sessions", {
    items: [
      {
        id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
        user_id: USER.id,
        username: USER.username,
        token_version: 3,
        created_at: T1,
        updated_at: T2,
        last_seen_at: T2,
      },
    ],
    total: 1,
    skip: 0,
    limit: 50,
  });
  answer("get", "/api/v1/projects/{project_id}/zadanie-na-disku", {
    content: "",
    relative_path: "docs/specs/versions/v1.8.0/ZADANIE.md",
  });
  // DEV-149: the new-version screen asks the cockpit for the number instead of computing it.
  answer("get", "/api/v1/projects/{project_id}/versions/next-number", { version_number: "1.8.0" });
  // DEV-73: NEX Inbox as it stood 12.10.2026 — an issued fix, an issued version a newer rate would count higher, a
  // version to issue, a fast fix waiting for its work kind, a version still in build.
  const issued = (amount: string, kind: "fix" | "change") => ({
    id: `aaaaaaaa-0000-4000-8000-${amount.replace(".", "").padStart(12, "0")}`,
    created_at: T1,
    work_kind: kind,
    base_sha: "ec28fb9d0000000000000000000000000000000",
    delivered_sha: "48de08076e594d4b224281b1befd0a5ba0fa9ca3",
    delivered_source: "verifikacia",
    tokenizer: "o200k_base (tiktoken 0.14.0)",
    tokens_code: 173_890,
    tokens_tests: 0,
    tokens_docs: 76_087,
    rate_code: "1",
    rate_docs: "1",
    amount_code_eur: null,
    amount_docs_eur: null,
    amount_eur: amount,
    replaces_id: null,
    replaced_at: null,
    lines: null,
  });
  const billingRow = {
    flow_type: "new_version",
    reason: null,
    work_kind: "change" as const,
    base_sha: "4f71c9c0000000000000000000000000000000000",
    delivered_sha: "ec28fb9d0000000000000000000000000000000",
    delivered_source_label: "stav, na ktorom prešla Verifikácia",
    tokens_code: 61_200,
    tokens_docs: 1_830,
    issued: null,
    differs: false,
    rate_code: "2",
    rate_docs: "1",
    own_rates: false,
  };
  answer("get", "/api/v1/projects/{slug}/billing", {
    rows: [
      { ...billingRow, version_id: VERSION.id, version_number: "1.8.0", state: "not_done", reason: "Verzia ešte nie je hotová — súpis sa robí z dodanej verzie.", delivered_sha: null, base_sha: null, preview_amount_eur: null, tokens_code: null, tokens_docs: null },
      { ...billingRow, version_id: RELEASED.id, version_number: "1.7.1", flow_type: "fast_fix", work_kind: "fix", state: "issued", issued: issued("0.00", "fix"), preview_amount_eur: "0.00" },
      { ...billingRow, version_id: "22222222-2222-4222-8222-222222222170", version_number: "1.7.0", state: "issued", issued: issued("249.98", "change"), preview_amount_eur: "423.87", differs: true },
      { ...billingRow, version_id: "22222222-2222-4222-8222-222222222160", version_number: "1.6.0", state: "ready", preview_amount_eur: "93.03", rate_code: "1.5", own_rates: true },
      {
        ...billingRow,
        version_id: "22222222-2222-4222-8222-222222222156",
        version_number: "1.5.6",
        flow_type: "fast_fix",
        work_kind: null,
        state: "needs_kind",
        reason:
          "Pri rýchlej oprave treba určiť, či opravovala chybu v dodanom kóde (záručná oprava), alebo robila zmenu (účtuje sa) — kokpit to nehádá.",
        preview_amount_eur: null,
      },
    ],
    issued_total_eur: "249.98",
    ready_total_eur: "124.06",
    needs_kind: 1,
  });
  answer("get", "/api/v1/versions/{version_id}/delivery-statement", {
    preview: {
      version_number: VERSION.version_number,
      blocked: null,
      work_kind: "change",
      base_sha: "48de08076e594d4b224281b1befd0a5ba0fa9ca3",
      delivered_sha: "af97f3721c65713dfbbfdb71cc3b0a81375dd80d",
      delivered_source: "verified",
      delivered_source_label: "overený stav kódu po Verifikácii",
      tokenizer: "claude",
      code: { tokens: 184_000, lines: 6_120 },
      tests: { tokens: 96_000, lines: 3_480 },
      docs: { tokens: 41_000, lines: 1_200 },
      files: [
        {
          path: "backend/apps/smb_writer/deliverer.py",
          kind: "code",
          excluded: null,
          lines: 412,
          tokens: 3_900,
        },
        {
          path: "backend/tests/integration/test_genesis_deliverer_survives_a_silent_share.py",
          kind: "tests",
          excluded: null,
          lines: 236,
          tokens: 2_100,
        },
      ],
      rate_code: "0.012",
      rate_docs: "0.006",
      settings_rate_code: "0.002",
      settings_rate_docs: "0.006",
      rate_code_own: true,
      rate_docs_own: false,
      amount_code_eur: "2124.00",
      amount_docs_eur: "246.00",
      amount_eur: "2370.00",
      cannot_issue: [],
      calibration: { eur_code: 2208, eur_docs: 246, per_1k_code: 12, per_1k_docs: 6, complete: true },
      // DEV-75 — the lines by the category of the task that wrote the code, a gift at 100 % and work without a task.
      version_category: "request",
      discounts: { request: "0", bug: "100", gift: "100" },
      settings_discounts: { request: "0", bug: "100", gift: "100" },
      discounts_own: { request: false, bug: false, gift: true },
      full_eur: "2454.00",
      lines: [
        {
          category: "request",
          kind: "code",
          tasked: true,
          label: "Požiadavka zákazníka — kód a skúšky",
          lines: 8_900,
          tokens: 170_000,
          rate: "0.012",
          full_eur: "2040.00",
          discount_pct: "0",
          discount_label: null,
          discount_eur: "0.00",
          amount_eur: "2040.00",
          tasks: [{ label: "28.1.1", title: "Doručovanie do NEX Genesis", lines: 8_900, tokens: 170_000 }],
        },
        {
          category: "request",
          kind: "code",
          tasked: false,
          label: "Práca bez úlohy — kód a skúšky (Požiadavka zákazníka)",
          lines: 300,
          tokens: 7_000,
          rate: "0.012",
          full_eur: "84.00",
          discount_pct: "0",
          discount_label: null,
          discount_eur: "0.00",
          amount_eur: "84.00",
          tasks: [],
        },
        {
          category: "request",
          kind: "docs",
          tasked: false,
          label: "Práca bez úlohy — dokumentácia (Požiadavka zákazníka)",
          lines: 1_200,
          tokens: 41_000,
          rate: "0.006",
          full_eur: "246.00",
          discount_pct: "0",
          discount_label: null,
          discount_eur: "0.00",
          amount_eur: "246.00",
          tasks: [],
        },
        {
          category: "gift",
          kind: "code",
          tasked: true,
          label: "Dar — pozornosť ICC — kód a skúšky",
          lines: 400,
          tokens: 7_000,
          rate: "0.012",
          full_eur: "84.00",
          discount_pct: "100",
          discount_label: "pozornosť ICC",
          discount_eur: "84.00",
          amount_eur: "0.00",
          tasks: [{ label: "28.2.1", title: "Tmavý režim", lines: 400, tokens: 7_000 }],
        },
      ],
      tasks: [
        {
          id: "aaaaaaaa-0000-4000-8000-000000028011",
          label: "28.1.1",
          title: "Doručovanie do NEX Genesis",
          epic_title: "Doručovanie faktúr",
          own_category: null,
          category: "request",
          lines: 8_900,
          tokens: 170_000,
        },
        {
          id: "aaaaaaaa-0000-4000-8000-000000028021",
          label: "28.2.1",
          title: "Tmavý režim",
          epic_title: "Doručovanie faktúr",
          own_category: "gift",
          category: "gift",
          lines: 400,
          tokens: 7_000,
        },
      ],
    },
    issued: [],
    discount_changes: [
      {
        created_at: T2,
        changed_by: "Mária Manažérová",
        previous: { request: null, bug: null, gift: null },
        current: { request: null, bug: null, gift: "100" },
      },
    ],
    rate_changes: [
      {
        created_at: T2,
        changed_by: "Mária Manažérová",
        previous_rate_code: null,
        previous_rate_docs: null,
        rate_code: "0.012",
        rate_docs: null,
      },
    ],
  });
}

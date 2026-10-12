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
import { answer, answerUntyped } from "./api";

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

const TASK_PLAN: TaskPlanResponse = {
  epic_count: 2,
  feat_count: 2,
  task_count: 3,
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
      uat_last_attempt_failed: false,
      prod_version: "1.7.1",
      prod_url: "https://magerstav-inbox.icc.sk",
      prod_last_deploy_at: "2026-10-11T19:53:00Z",
      prod_last_deploy_detail: "Nasadené na MAGER; verejná adresa odpovedá.",
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
      uat_last_attempt_failed: false,
      prod_version: null,
      prod_url: null,
      prod_last_deploy_at: null,
      prod_last_deploy_detail: null,
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

export function registerFixtures(): void {
  answer("get", "/api/v1/auth/me", USER);
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
        created_at: T0,
        updated_at: T1,
      },
    ],
    total: 1,
    skip: 0,
    limit: 50,
  });
  answer("get", "/api/v1/projects/{slug}/customers", [CUSTOMER, NEW_CUSTOMER]);
  answer("get", "/api/v1/projects/{slug}/deploy-matrix", DEPLOY_MATRIX);
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
  answer("get", "/api/v1/poradca/projects/{slug}/conversations", [
    {
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
    },
  ]);
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
      amount_code_eur: "2208.00",
      amount_docs_eur: "246.00",
      amount_eur: "2454.00",
      cannot_issue: [],
      calibration: { eur_code: 2208, eur_docs: 246, per_1k_code: 12, per_1k_docs: 6, complete: true },
    },
    issued: [],
  });
}

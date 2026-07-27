// Per-user per-role agent model/effort config (CR-NS-040, E3(b/c)).
// Mirrors backend/schemas/user_agent_setting.py.

import type { components } from "@/services/api/pipeline.generated";

// The PIPELINE agent role (NOT the user's ri/ha/shu access role). v2 (CR-V2-001/007): the 5-role waterfall
// collapsed to the two v2 agents — the AI Agent (doer) + the independent Auditor. DB values (underscore).
export type PipelineAgentRole = "ai_agent" | "auditor";

// Aliasované z GENEROVANÉHO kontraktu, nie prepísané ručne: backend deklaruje zoznam ako `Literal`,
// FastAPI ho vydá ako OpenAPI enum a `npm run codegen` ho donesie sem. Pridanie modelu na backende tak
// automaticky preteče do FE — ručná kópia sa pri poslednej zmene modelu rozišla a type-check ju chytil
// až po regenerácii (2026-07-27).
export type AgentModel = NonNullable<components["schemas"]["UserAgentSettingRead"]["model"]>;

// The 5 effort levels `claude --effort` accepts (NO ultracode — the CLI ignores it).
export type AgentEffort = "low" | "medium" | "high" | "xhigh" | "max";

export interface UserAgentSettingRead {
  agent_role: PipelineAgentRole;
  model: AgentModel | null;
  effort: AgentEffort | null;
  // CR-V2-038: the model the AI Agent spawns its dynamic helpers on (only meaningful for ai_agent).
  // null = the dispatch default (a cheap/fast model for bulk work).
  helper_model: AgentModel | null;
}

export interface UserAgentSettingUpsert {
  model: AgentModel | null;
  effort: AgentEffort | null;
  helper_model: AgentModel | null;
}

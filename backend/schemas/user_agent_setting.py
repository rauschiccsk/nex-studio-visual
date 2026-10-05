"""Pydantic schemas for per-user per-role agent model/effort config (CR-NS-040, E3(b/c)).

The cockpit applies the project owner's row at dispatch. ``model``/``effort`` are validated by
enums HERE (not a DB CHECK) so the CLI's accepted sets can evolve without a migration:

* effort = the 5 levels ``claude --effort`` accepts (verified 2026-06-13: low/medium/high/xhigh/max
  — NO ``ultracode``; the CLI silently ignores it and falls back to default effort).
* model = the model FAMILY, never a version (ICCINT-167). ``claude --model opus`` runs the newest Opus
  (measured 2026-10-05 with CLI 2.1.289 — the result is recorded in ``docs/specs/poradca.md`` §4.6) and the
  CLI updates itself on the host, so a model roll needs no change here. Which version really ran is read
  from the run record (:func:`backend.services.user_agent_settings.model_options`).

``protected_namespaces=()`` lets a field be literally named ``model`` (pydantic v2 otherwise warns it
collides with the reserved ``model_`` namespace).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict

# The agent whose model/effort a row sets: the two PIPELINE agents {ai_agent, auditor} (same set as
# OrchestratorSession; v2.0.0 CR-V2-001) + Poradca (ICCINT-167 — not a pipeline role, it runs beside the
# build). NOT the user's ri/ha/shu access role.
PipelineAgentRole = Literal["ai_agent", "auditor", "poradca"]
# The model families ``claude --model`` resolves to their newest version — strongest first (the order
# the Nastavenia select shows). The Agent tool a helper is spawned with takes exactly these names too.
AgentModel = Literal["opus", "sonnet", "haiku"]
#: The same families as a tuple — the single source for the price families (``metrics``) and the options.
MODEL_FAMILIES: tuple[str, ...] = get_args(AgentModel)
# The 5 effort levels claude --effort accepts (NO ultracode — see module docstring).
AgentEffort = Literal["low", "medium", "high", "xhigh", "max"]


class UserAgentSettingRead(BaseModel):
    """One per-user per-role config row."""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    agent_role: PipelineAgentRole
    model: Optional[AgentModel] = None
    effort: Optional[AgentEffort] = None
    # CR-V2-038: the model the AI Agent spawns its dynamic HELPERS on (only meaningful for ai_agent).
    # Unset → the dispatch default (Haiku — cheap bulk). Same enum as ``model``.
    helper_model: Optional[AgentModel] = None


class UserAgentSettingUpsert(BaseModel):
    """PUT body — model and/or effort (+ AI Agent helper_model) for a role (any may be unset = no flag)."""

    model_config = ConfigDict(protected_namespaces=())

    model: Optional[AgentModel] = None
    effort: Optional[AgentEffort] = None
    helper_model: Optional[AgentModel] = None


class AgentModelOption(BaseModel):
    """One family the Nastavenia offers + the version that last REALLY ran on it (ICCINT-167).

    ``last_run_model`` is the full id the CLI reported (``modelUsage``) on the newest recorded run of the
    family — the honest answer to "what does Opus mean today"; ``None`` until the family has run once."""

    model_config = ConfigDict(protected_namespaces=())

    id: AgentModel
    last_run_model: Optional[str] = None
    last_run_at: Optional[datetime] = None

"""Pydantic schemas for per-user per-role agent model/effort config (CR-NS-040, E3(b/c)).

The cockpit applies the project owner's row at dispatch. ``model``/``effort`` are validated by
enums HERE (not a DB CHECK) so the CLI's accepted sets can evolve without a migration:

* effort = the 5 levels ``claude --effort`` accepts (verified 2026-06-13: low/medium/high/xhigh/max
  — NO ``ultracode``; the CLI silently ignores it and falls back to default effort).
* model = the 3 dispatchable model IDs.

``protected_namespaces=()`` lets a field be literally named ``model`` (pydantic v2 otherwise warns it
collides with the reserved ``model_`` namespace).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

# The PIPELINE agent role {ai_agent, auditor} (same set as OrchestratorSession; v2.0.0 CR-V2-001), NOT
# the user's ri/ha/shu access role.
PipelineAgentRole = Literal["ai_agent", "auditor"]
# Dispatchable model IDs (claude --model accepts the full name).
AgentModel = Literal["claude-opus-5", "claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"]
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

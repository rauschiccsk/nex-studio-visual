"""Agent model is stored as a family, not a version (ICCINT-167).

**What happened.** Settings offered a list of four versions written in code and the default model was hard-coded
Opus 5. When Opus 5.5 came out, the cockpit did not know it — it could not be chosen without changing the app and all
builds kept running on Opus 5. Director 2026-10-05: *„aby som nemusel opravovať aplikáciu, ak sa zmení
verzia modelu."*

**Why a family.** ``claude --model opus`` launches the latest Opus and Claude Code updates itself on the machine.
The cockpit therefore stores only the family (``opus``, ``sonnet``, ``haiku``); which version actually ran is shown by
the run log.

**What the migration does.** Converts stored names to the family by the word in the name; a name with no known family
it clears (= default model), because the new API constraint would no longer accept it. On the live cockpit on the day
of the change there was not a single row in the table — the migration protects other databases.

**Downgrade** clears the family to the default: nobody remembers the specific version it came from
and an invented one would be worse than the default.

Revision ID: 101
Revises: 100
Create Date: 2026-10-05
"""

from typing import Sequence, Union

from alembic import op

revision: str = "101"
down_revision: Union[str, None] = "100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Strongest first, the same order as ``AgentModel`` — a name containing two family words is not a thing,
# but the order keeps the mapping deterministic if one ever appears.
_FAMILIES = ("opus", "sonnet", "haiku")
_COLUMNS = ("model", "helper_model")


def _family_case(column: str) -> str:
    whens = " ".join(f"WHEN lower({column}) LIKE '%{fam}%' THEN '{fam}'" for fam in _FAMILIES)
    return f"CASE {whens} ELSE NULL END"


def upgrade() -> None:
    for column in _COLUMNS:
        op.execute(f"UPDATE user_agent_settings SET {column} = {_family_case(column)} WHERE {column} IS NOT NULL")


def downgrade() -> None:
    families = ", ".join(f"'{fam}'" for fam in _FAMILIES)
    for column in _COLUMNS:
        op.execute(f"UPDATE user_agent_settings SET {column} = NULL WHERE {column} IN ({families})")

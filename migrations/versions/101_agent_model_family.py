"""Model agenta sa ukladá ako rodina, nie verzia (ICCINT-167).

**Čo sa dialo.** Nastavenia ponúkali zoznam štyroch verzií zapísaný v kóde a predvolený model bol natvrdo
Opus 5. Keď vyšiel Opus 5.5, kokpit o ňom nevedel — bez zásahu do aplikácie sa nedal zvoliť a všetky
stavby ďalej bežali na Opuse 5. Director 05.10.2026: *„aby som nemusel opravovať aplikáciu, ak sa zmení
verzia modelu."*

**Prečo rodina.** ``claude --model opus`` spustí najnovší Opus a Claude Code sa na stroji aktualizuje sám.
Kokpit preto ukladá len rodinu (``opus``, ``sonnet``, ``haiku``); ktorá verzia naozaj bežala, ukazuje
záznam behov.

**Čo migrácia robí.** Uložené úplné mená prevedie na rodinu podľa slova v mene; meno bez známej rodiny
vymaže (= predvolený model), lebo nové obmedzenie v API by ho už neprečítalo. Na ostrom kokpite v deň
zmeny nebol v tabuľke ani jeden riadok — migrácia chráni ostatné databázy.

**Späť** sa rodina vymaže na predvolené: konkrétnu verziu, z ktorej vznikla, si nikto nepamätá
a vymyslená by bola horšia než predvolená.

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

"""Rozhovor s Poradcom si pamätá, s ktorou chartou beží (DEV-30, v4.43.9).

**Čo sa dialo.** Claude Code berie chartu Poradcu (``--append-system-prompt``) len pri prvej otázke rozhovoru; pri
pokračovaní (``--resume``) novú ignoruje (zmerané 08.10.2026). Zmena charty sa tak k rozhovorom, ktoré už bežia,
nedostala nikdy — Director sa pýtal v rozhovore z rána a platilo ranné pravidlo.

**Čo mení.** ``poradca_conversations.charter_sha`` — odtlačok charty, ktorú rozhovor naposledy dostal. Keď sa
charta zmení, kokpit ju pridá do najbližšej otázky v jej texte (raz) a odtlačok prepíše. Rozhovory spred tejto
zmeny majú prázdny odtlačok, takže aktuálnu chartu dostanú pri najbližšej otázke.

**Späť** stĺpec zmizne.

Revision ID: 107
Revises: 106
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "107"
down_revision: Union[str, None] = "106"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("poradca_conversations", sa.Column("charter_sha", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("poradca_conversations", "charter_sha")

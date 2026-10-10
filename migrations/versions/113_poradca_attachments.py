"""Screenshots attached to a question for Poradca (DEV-52).

Director 10.10.2026: „Do editoru pre Poradcu chcem zabudovať možnosť priania screenshotu podobne ako je to možné
v Claude Desktop.“ The question message records which images it carried (``id``, ``name``, ``mime``,
``size_bytes``); the files lie in Poradca's data next to the conversation's record, never in the project.

Revision ID: 113
Revises: 112
Create Date: 2026-10-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "113"
down_revision: Union[str, None] = "112"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("poradca_messages", sa.Column("attachments", JSONB, nullable=False, server_default="[]"))


def downgrade() -> None:
    op.execute("ALTER TABLE poradca_messages DROP COLUMN IF EXISTS attachments")

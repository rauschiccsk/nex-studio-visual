"""Deleting a user no longer takes their Poradca conversations or their cost with it (ICCINT-169, v4.43.2).

**What happened.** The foreign key ``poradca_conversations.author_id`` had ON DELETE CASCADE: deleting a user
(user trash in the cockpit) deleted their conversations with answers — and the cost of those answers from the Costs of
the project, though really spent. Projects and bugs already block user deletion (RESTRICT); conversations did not.

**What it changes.** Key to ON DELETE RESTRICT; before deleting, the cockpit says why it cannot and offers deactivation
(``user_service._has_restrict_dependencies``).

**Downgrade** returns the key to CASCADE.

Revision ID: 105
Revises: 104
Create Date: 2026-10-06
"""

from typing import Sequence, Union

from alembic import op

revision: str = "105"
down_revision: Union[str, None] = "104"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FK = "poradca_conversations_author_id_fkey"


def upgrade() -> None:
    op.drop_constraint(_FK, "poradca_conversations", type_="foreignkey")
    op.create_foreign_key(_FK, "poradca_conversations", "users", ["author_id"], ["id"], ondelete="RESTRICT")


def downgrade() -> None:
    op.drop_constraint(_FK, "poradca_conversations", type_="foreignkey")
    op.create_foreign_key(_FK, "poradca_conversations", "users", ["author_id"], ["id"], ondelete="CASCADE")

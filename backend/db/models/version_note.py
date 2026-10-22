"""„Kde sme skončili“ — a note at a version: why its development stands and what next, for the people (DEV-151).

Director 20.10.2026: „Aby v prípade, že vývoj projektu z nejakej príčiny nedokončíme (príde niečo dôležitejšie) mali
by sme presnú informáciu o tom, kde a ako pokračovať v projekte.“ The Prehľad (DEV-150) says where a build stands from
the cockpit's records; why the people stopped and what they agreed only a person can say. The Poznámky page did not:
its notes are private and bound to no version. A note here belongs to the version, carries its author and time, is
read by everyone who may work on the project, and older notes stay. The agent never gets it — it is no instruction.
"""

from __future__ import annotations

from sqlalchemy import TIMESTAMP, Column, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, UUIDMixin


class VersionNote(Base, UUIDMixin):
    __tablename__ = "version_notes"

    version_id = Column(UUID(as_uuid=True), ForeignKey("versions.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    text = Column(Text, nullable=False)
    # The moment it was written — ``now()`` would be the start of its transaction, the same for two notes in one.
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.clock_timestamp())

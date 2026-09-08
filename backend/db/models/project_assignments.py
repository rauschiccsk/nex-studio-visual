"""Kto komu zveril projekt (ICCINT-78).

Vlastníctvo je ``Project.created_by`` — táto tabuľka ho nenahrádza, len si pamätá, ako sa menilo.
Dôvody sú v migrácii 095; v skratke: presun slúži aj na zastupovanie počas neprítomnosti, takže bez
záznamu by sa po čase nedalo rozoznať trvalé odovzdanie od týždňovej výpožičky — a prvý riadok si drží
pôvodného zakladateľa, ktorého by prepisovanie ``created_by`` inak zmazalo.
"""

from sqlalchemy import TIMESTAMP, Column, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, UUIDMixin


class ProjectAssignment(Base, UUIDMixin):
    """Jeden presun projektu — od koho, komu, kto ho urobil.

    Zámerne BEZ ``updated_at``: zápis o tom, kto komu čo zveril, je udalosť. Udalosti sa nemenia; keď je
    zle, pridá sa ďalší riadok, nie sa prepíše starý.
    """

    __tablename__ = "project_assignments"
    #: Deklarované aj tu, nielen v migrácii — sada stráži, aby sa model a databáza nerozišli.
    __table_args__ = (Index("ix_project_assignments_project", "project_id", "created_at"),)

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    #: NULL len pri prvom riadku (založenie projektu) — dovtedy nepatril nikomu.
    from_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    to_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    #: Presúvať smie jedine admin a musí byť vidieť ktorý — preto NOT NULL.
    assigned_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

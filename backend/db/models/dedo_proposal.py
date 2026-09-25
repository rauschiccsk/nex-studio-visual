"""Zadanie, ktoré Dedo pripravil pre prácu, ktorá sa ešte nezačala (ICCINT-152).

**Prečo vlastná tabuľka a nie ``pipeline_message``.** Návrh do BEŽIACEJ stavby je správa v denníku tej
stavby a žije v ``pipeline_message``. Tu stavba neexistuje — a ``pipeline_message`` vyžaduje ``stage``,
ktorý je navyše obmedzený na fázy priebehu. Zapísať tam ``priprava`` by bol vymyslený údaj v zázname,
ktorý sa nikdy neprepisuje, a znečistil by históriu verzie, s ktorou tá práca nemá nič spoločné.

**Čo tomu predchádzalo.** 25.09.2026 prestalo na MÁGERSTAVE fungovať spúšťanie NEX Inboxu z NEX
Managera. Oprava patrila do NEX Inboxu a Director požiadal: *„zapíš to zadanie do kokpitu ako návrh."*
Nešlo to — Dedove dvere vedia položiť návrh len na rozbehnutú stavbu, teda práve vtedy, keď už zadanie
netreba. Text som mu musel podať do ruky, aby ho pri spúšťaní rýchlej opravy vložil. Presne tomu mali
tie dvere zabrániť.

⚠️ **Návrh sám nikdy nič nespustí.** Doručenie je vždy kliknutie Manažéra — rovnako ako pri návrhu do
bežiacej stavby. Preto tu nie je nič, čo by sa dalo považovať za príkaz: je to text a sloveso.
"""

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.db.models.base import Base, TimestampMixin, UUIDMixin

#: Čo Dedo navrhuje spraviť. ``fast_fix`` založí opravnú verziu a SPUSTÍ ju so zadaním; ``new_version``
#: založí verziu ako koncept so zadaním v popise a nespustí nič. Obe sú veci, ktoré Manažér v kokpite
#: robí aj sám — návrh mu ich len predvyplní.
PROJECT_PROPOSAL_ACTIONS = ("fast_fix", "new_version")

#: Návrh čaká na Manažéra.
PROPOSED = "proposed"
#: Manažér ho poslal ďalej — vznikla z neho verzia.
SENT = "sent"
#: Manažér ho zamietol.
REJECTED = "rejected"
#: Dedo napísal novší. ⚠️ Odlíšené od ``rejected`` zámerne: záznam nesmie tvrdiť, že Manažér rozhodol
#: o niečom, čo nikdy nevidel.
SUPERSEDED = "superseded"

PROJECT_PROPOSAL_STATUSES = (PROPOSED, SENT, REJECTED, SUPERSEDED)


class DedoProjectProposal(Base, UUIDMixin, TimestampMixin):
    """Zadanie od Deda, pripnuté na PROJEKT — nie na stavbu, lebo tá ešte nebeží."""

    __tablename__ = "dedo_project_proposal"

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content = Column(Text, nullable=False)
    proposed_action = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, server_default=PROPOSED)
    #: Kto o návrhu rozhodol. Prázdne, kým je otvorený — a prázdne aj pri ``superseded``, lebo vtedy
    #: nerozhodol nikto.
    resolved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    #: Verzia, ktorá z návrhu vznikla. Prázdne, kým sa neodoslal.
    version_id = Column(UUID(as_uuid=True), ForeignKey("versions.id", ondelete="SET NULL"), nullable=True)

    project = relationship("Project")

    __table_args__ = (
        CheckConstraint(
            "proposed_action IN ('fast_fix', 'new_version')",
            name="ck_dedo_project_proposal_action",
        ),
        CheckConstraint(
            "status IN ('proposed', 'sent', 'rejected', 'superseded')",
            name="ck_dedo_project_proposal_status",
        ),
        # ⚠️ Najviac JEDEN otvorený návrh na projekt, a drží to DATABÁZA. Služba síce staršie návrhy
        # pri zápise archivuje, ale pravidlo, ktoré stojí len na poradí príkazov v jednej funkcii,
        # padne pri druhom zapisovateľovi alebo pri dvoch behoch naraz — a Manažér by potom videl
        # dve zadania a nevedel, ktoré platí.
        Index(
            "uq_dedo_project_proposal_open",
            "project_id",
            unique=True,
            postgresql_where=Column("status") == PROPOSED,
        ),
    )

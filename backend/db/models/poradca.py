"""Poradca — rozhovory s agentom, ktorý len číta a radí (ICCINT-167, ``docs/specs/poradca.md``).

**Prečo vlastné tabuľky a nie ``pipeline_message``.** Poradca beží VEDĽA stavby: neprepína jej stav,
nečaká na ňu a agent stavby o rozhovore nevie. Správa v ``pipeline_message`` je zápis do denníka stavby,
ktorý číta agent aj Manažér ako históriu verzie — rozhovor Poradcu by tam bol cudzí hlas. Navyše Poradca
sa smie pýtať aj na celý projekt, keď žiadna verzia nebeží.

**Čo sa ukladá z priebehu.** Len KROKY — nástroj a cieľ („Read · backend/x.py", „logy · uat backend") —
nikdy obsah, ktorý nástroj vrátil. Obsah môže niesť čokoľvek, čo agent videl; do databázy a do
prehliadača ide až odpoveď, ktorá prešla filtrom tajomstiev (návrh 4.1, nález B7 nezávislej kontroly).
"""

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Float, ForeignKey, Identity, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin

#: Kto v rozhovore hovorí: človek (autor rozhovoru) alebo Poradca.
AUTHOR_HUMAN = "human"
AUTHOR_PORADCA = "poradca"
MESSAGE_AUTHORS = (AUTHOR_HUMAN, AUTHOR_PORADCA)

#: Stav odpovede Poradcu. Otázka človeka je vždy ``done`` — je hotová v okamihu, keď ju odoslal.
RUNNING = "running"
DONE = "done"
FAILED = "failed"
STOPPED = "stopped"
MESSAGE_STATUSES = (RUNNING, DONE, FAILED, STOPPED)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class PoradcaConversation(Base, UUIDMixin, TimestampMixin):
    """Jeden rozhovor jedného človeka k jednému projektu.

    ``version_id`` je o čom sa rozprávame — prázdne znamená „celý projekt". Dá sa zmeniť počas rozhovoru,
    lebo človek sa často pýta na verziu, ktorá práve beží, a potom na tú predchádzajúcu.

    ``claude_session_id`` je sedenie Claude Code, ktoré rozhovor nesie cez ``--resume``; jeho záznam leží
    v priečinku TOHTO rozhovoru, nie v priečinku projektu, kde sú záznamy agenta stavby (nález B3).
    """

    __tablename__ = "poradca_conversations"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    version_id = Column(UUID(as_uuid=True), ForeignKey("versions.id", ondelete="SET NULL"), nullable=True)
    #: Autor rozhovoru — prihlásený účet. Rozhovor vidí on a účet admin. RESTRICT (ICCINT-169): zmazanie autora by
    #: zobralo rozhovory aj cenu odpovedí z Nákladov — autora treba deaktivovať, nie zmazať.
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    #: Názov podľa prvej otázky (skrátený), aby sa rozhovory dali v zozname rozlíšiť.
    title = Column(String(200), nullable=False)
    claude_session_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    #: DEV-30: fingerprint of the charter this conversation last received. Claude Code takes the charter only on
    #: the first question (``--append-system-prompt`` is ignored on ``--resume``); when it changes, the runner adds
    #: the current one to the next question's text. Empty for conversations started before DEV-30.
    charter_sha = Column(String(64), nullable=True)
    #: Kedy ho autor (alebo admin) vymazal. Riadok ostáva len kvôli Nákladom: text, kroky aj názov sú preč,
    #: zo správ ostala spotreba a čas. Pre rozhranie taký rozhovor neexistuje (404).
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_poradca_conversations_project_author", "project_id", "author_id"),)


class PoradcaMessage(Base, UUIDMixin, TimestampMixin):
    """Jedna správa rozhovoru — otázka človeka alebo odpoveď Poradcu.

    Odpoveď vzniká ako ``running`` a dopĺňa sa: ``steps`` (nástroj a cieľ, nikdy obsah), potom text,
    spotreba (``usage`` — tokeny a úplné meno modelu tak, ako ho hlási Claude Code) a trvanie.
    """

    __tablename__ = "poradca_messages"

    conversation_id = Column(
        UUID(as_uuid=True), ForeignKey("poradca_conversations.id", ondelete="CASCADE"), nullable=False
    )
    #: Poradie v rozhovore — monotónne aj pri rovnakom ``created_at`` v jednej transakcii.
    seq = Column(BigInteger, Identity(), nullable=False)
    author = Column(String(16), nullable=False)
    content = Column(Text, nullable=False, server_default="")
    #: ``[{"tool": "Read", "target": "backend/x.py"}, …]`` — čo Poradca robil, bez obsahu.
    steps = Column(JSONB, nullable=False, server_default="[]")
    #: DEV-52 — screenshots attached to a question: ``[{"id", "name", "mime", "size_bytes"}, …]``; the files lie in
    #: Poradca's data next to the conversation's record (``sandbox.attachments_dir``), never in the project.
    attachments = Column(JSONB, nullable=False, server_default="[]")
    status = Column(String(16), nullable=False, server_default=DONE)
    #: ``{"input_tokens", "output_tokens", "model"}`` — ten istý tvar ako ``payload.usage`` stavby, aby sa
    #: Poradca dal oceniť tou istou cestou ako stavba.
    usage = Column(JSONB, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    #: Prečo odpoveď zlyhala — veta pre človeka, bez tajomstiev.
    error = Column(Text, nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    #: Verzia, ku ktorej sa otázka vzťahovala v čase, keď padla — rozhovor môže verziu zmeniť, Náklady
    #: verzie však musia sčítať len to, čo sa na ňu naozaj pýtalo. Prázdne = celý projekt.
    version_id = Column(UUID(as_uuid=True), ForeignKey("versions.id", ondelete="SET NULL"), nullable=True)
    #: DEV-29: the backlog item the Manažér saved from this answer's request — a second click returns it.
    captured_backlog_item_id = Column(
        UUID(as_uuid=True), ForeignKey("backlog_items.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(f"author IN ({_in(MESSAGE_AUTHORS)})", name="ck_poradca_messages_author"),
        CheckConstraint(f"status IN ({_in(MESSAGE_STATUSES)})", name="ck_poradca_messages_status"),
        Index("ix_poradca_messages_conversation_seq", "conversation_id", "seq"),
    )

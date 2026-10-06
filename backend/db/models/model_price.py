"""Cenník modelov Anthropic, ktorým kokpit oceňuje spotrebu agentov (ICCINT-168).

Ceny sa nezadávajú ručne: kokpit ich vyčíta z ťahov, ktoré zaplatil Claude Code (rozdiel jeho súčtov ceny
v zázname sedenia), a overí, že nimi ceny Claude Code zopakuje — :mod:`backend.services.model_pricing`.
Director 06.10.2026: „Ak tie ceny nepoužívame už prosím ich upratať, je zmetkujúce ak niečo tam je uvedené
a v skutočnosti sa používa niečo iné."

Jeden riadok = cenník jedného modelu od ``valid_from``. Keď Anthropic zmení cenu, pribudne riadok nový —
starý ostáva, lebo ním sa počítali staršie ťahy. Kurz eura je zviazaný s cenníkom: stiahne sa raz, keď cenník
vznikne, nie pri každom zobrazení (Director: „kurz zistiť z verejne dostupného zdroja len pri zmene ceny").
"""

from sqlalchemy import TIMESTAMP, Column, Date, Float, Integer, String, UniqueConstraint

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class ModelPrice(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "model_prices"

    #: Úplné meno modelu tak, ako ho hlási Claude Code (``claude-<rodina>-<verzia>``) — cenník je viazaný na verziu.
    model = Column(String(100), nullable=False)
    #: Prvý ťah, ktorý tento cenník pokrýva.
    valid_from = Column(TIMESTAMP(timezone=True), nullable=False)
    #: Ceny v dolároch za milión tokenov.
    input_usd = Column(Float, nullable=False)
    output_usd = Column(Float, nullable=False)
    cache_read_usd = Column(Float, nullable=False)
    cache_write_usd = Column(Float, nullable=False)
    #: Cena jedného vyhľadávania na webe — Claude Code ho účtuje zvlášť. Prázdne, kým model žiadne nemal.
    web_search_usd = Column(Float, nullable=True)
    #: Koľko dolárov stojí jedno euro (referenčný kurz ECB, tak ako ho banka zverejňuje). Prázdne, kým sa
    #: kurz nepodarilo stiahnuť — cena v eurách sa dovtedy nevyčísli.
    eur_usd = Column(Float, nullable=True)
    rate_date = Column(Date, nullable=True)
    rate_source = Column(String(200), nullable=True)
    #: Na koľkých ťahoch zaplatených Claude Code je cenník overený, a najväčšia relatívna odchýlka od jeho cien.
    observations = Column(Integer, nullable=False)
    max_deviation = Column(Float, nullable=False)
    #: Najnovší zaplatený ťah, ktorý sa s cenníkom už porovnal (novšie sa porovnajú pri ďalšej kontrole).
    checked_until = Column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("model", "valid_from", name="uq_model_prices_model_valid_from"),)

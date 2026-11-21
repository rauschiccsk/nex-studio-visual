"""When the cockpit last ran a periodic maintenance job (DEV-484) — kept in the database, so a restart does not
reset it.

21.11.2026: the daily pruning of the CI runners' Docker (DEV-435) slept a day after every start of the backend and the
cockpit was deployed several times a day — in 72 hours it never ran once.
"""

from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import TIMESTAMP

from backend.db.models.base import Base, TimestampMixin


class MaintenanceRun(Base, TimestampMixin):
    __tablename__ = "maintenance_runs"

    job = Column(String(64), primary_key=True)
    last_run_at = Column(TIMESTAMP(timezone=True), nullable=False)

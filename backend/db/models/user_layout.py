"""How a person arranged the cockpit's screen — kept on the server, so it is the same on every computer (DEV-82).

12.10.2026 the Director: the widths of the side panels should change, and the cockpit should remember them
„pre každého užívateľa osobitne“. One row per user: the width of the sidebar with the tabs, whether it is collapsed
to icons, and the width of the task plan. An empty width = the default; the bounds are ``user_layout.LIMITS`` (the
service) and the database refuses anything outside them.
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Integer, false
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin


class UserLayout(Base, TimestampMixin):
    __tablename__ = "user_layouts"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    sidebar_width = Column(Integer, nullable=True)
    sidebar_collapsed = Column(Boolean, nullable=False, server_default=false())
    plan_width = Column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint("sidebar_width BETWEEN 180 AND 360", name="ck_user_layouts_sidebar_width"),
        CheckConstraint("plan_width BETWEEN 280 AND 640", name="ck_user_layouts_plan_width"),
    )

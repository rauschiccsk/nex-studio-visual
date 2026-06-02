"""Drop dead in-app design/execution pipeline tables (CR-NS-008).

Removes the 15 tables backing the pre-agent in-app design/execution
pipeline (architect chat, in-app spec/UI authoring, task-plan generation,
delegate-to-CC, execution logs, auto-fix, guardian review, project cost
report). Their models, routes, schemas and services were deleted in
CR-NS-008.

Multi-module support is PRESERVED — ``project_modules`` and
``module_dependencies`` are NOT dropped, and ``epics.module_id`` stays.
``architect_sessions`` / ``design_documents`` carried a
``module_id → project_modules`` FK; that FK vanishes with the table while
``project_modules`` itself remains.

``downgrade()`` recreates all 15 tables by mirroring their original create
migrations (002/010/011/012/017/018/019/022/027/035/036).

Revision ID: 048
Revises: 047
Create Date: 2026-06-02

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "048"
down_revision: Union[str, None] = "047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # FK-safe order — drop referencing (child) tables before referenced
    # (parent) tables. ``drop_table`` cascades each table's own indexes.
    op.drop_table("architect_messages")  # → architect_sessions
    op.drop_table("professional_spec_chat_messages")  # → professional_specifications
    op.drop_table("ui_design_chat_messages")  # → ui_designs
    op.drop_table("auto_fix_attempts")  # → delegations
    op.drop_table("execution_logs")  # → delegations
    op.drop_table("guardian_reviews")  # → delegations
    op.drop_table("delegations")  # → bug_fix_tasks
    op.drop_table("professional_specifications")  # → raw_specifications
    op.drop_table("bug_fix_tasks")
    op.drop_table("architect_sessions")
    op.drop_table("raw_specifications")
    op.drop_table("ui_designs")
    op.drop_table("guardian_precedents")
    op.drop_table("report_configs")
    op.drop_table("design_documents")


def downgrade() -> None:
    """Recreate the 15 dropped tables (parents before children)."""
    # 1. design_documents (012)
    op.create_table(
        "design_documents",
        sa.Column(
            "project_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "module_id",
            sa.UUID(),
            nullable=True,
        ),
        sa.Column("doc_type", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "approved_by",
            sa.UUID(),
            nullable=True,
        ),
        sa.Column(
            "approved_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["module_id"], ["project_modules.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "doc_type IN ('design', 'behavior')",
            name="ck_design_documents_doc_type",
        ),
    )
    op.create_index(
        "ix_design_documents_project_id",
        "design_documents",
        ["project_id"],
    )
    op.create_index(
        "ix_design_documents_module_id",
        "design_documents",
        ["module_id"],
    )
    op.create_index(
        "ix_design_documents_project_module_type",
        "design_documents",
        ["project_id", "module_id", "doc_type"],
    )

    # 2. report_configs (019)
    op.create_table(
        "report_configs",
        sa.Column(
            "project_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "senior_hourly_rate_eur",
            sa.Numeric(precision=10, scale=4),
            server_default="75.0000",
            nullable=False,
        ),
        sa.Column(
            "junior_hourly_rate_eur",
            sa.Numeric(precision=10, scale=4),
            server_default="35.0000",
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="uq_report_configs_project_id"),
    )

    # 3. guardian_precedents (002)
    op.create_table(
        "guardian_precedents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pattern_hash", sa.String(64), nullable=False),
        sa.Column("pattern_description", sa.Text(), nullable=False),
        sa.Column("verdict", sa.String(10), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pattern_hash", name="uq_guardian_precedents_pattern_hash"),
        sa.CheckConstraint(
            "verdict IN ('allow', 'notice', 'block')",
            name="ck_guardian_precedents_verdict",
        ),
    )

    # 4. raw_specifications (017)
    op.create_table(
        "raw_specifications",
        sa.Column(
            "project_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column(
            "input_format",
            sa.String(20),
            nullable=False,
            server_default="text",
        ),
        sa.Column(
            "language",
            sa.String(10),
            nullable=False,
            server_default="sk",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_by",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "input_format IN ('text', 'pdf', 'docx')",
            name="ck_raw_specifications_input_format",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'failed')",
            name="ck_raw_specifications_status",
        ),
    )
    op.create_index(
        "ix_raw_specifications_project_id",
        "raw_specifications",
        ["project_id"],
    )
    op.create_index(
        "ix_raw_specifications_status",
        "raw_specifications",
        ["status"],
    )

    # 5. ui_designs (027)
    op.create_table(
        "ui_designs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("html_preview", sa.Text(), nullable=True),
        sa.Column("approved_by", sa.UUID(), nullable=True),
        sa.Column(
            "approved_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ui_designs_project_id", "ui_designs", ["project_id"])

    # 6. architect_sessions (010)
    op.create_table(
        "architect_sessions",
        sa.Column(
            "project_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "module_id",
            sa.UUID(),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_by",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "closed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["module_id"], ["project_modules.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('active', 'closed')",
            name="ck_architect_sessions_status",
        ),
    )
    op.create_index(
        "ix_architect_sessions_project_id",
        "architect_sessions",
        ["project_id"],
    )
    op.create_index(
        "ix_architect_sessions_module_id",
        "architect_sessions",
        ["module_id"],
    )

    # 7. professional_specifications (018) — FK → raw_specifications
    op.create_table(
        "professional_specifications",
        sa.Column(
            "raw_spec_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "approved_by",
            sa.UUID(),
            nullable=True,
        ),
        sa.Column(
            "approved_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["raw_spec_id"], ["raw_specifications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_professional_specifications_raw_spec_id",
        "professional_specifications",
        ["raw_spec_id"],
    )
    op.create_index(
        "ix_professional_specifications_project_id",
        "professional_specifications",
        ["project_id"],
    )

    # 8. bug_fix_tasks (022)
    op.create_table(
        "bug_fix_tasks",
        sa.Column("bug_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("task_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="todo", nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=True),
        sa.Column("actual_minutes", sa.Integer(), nullable=True),
        sa.Column("checklist_type", sa.String(length=30), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('todo', 'in_progress', 'done', 'failed')",
            name="ck_bug_fix_tasks_status",
        ),
        sa.CheckConstraint(
            "task_type IN ('backend', 'frontend', 'migration', 'test', 'docs')",
            name="ck_bug_fix_tasks_task_type",
        ),
        sa.ForeignKeyConstraint(["bug_id"], ["bugs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bug_id", "number", name="uq_bug_fix_tasks_bug_id_number"),
    )
    op.create_index(op.f("ix_bug_fix_tasks_bug_id"), "bug_fix_tasks", ["bug_id"], unique=False)
    op.create_index(op.f("ix_bug_fix_tasks_status"), "bug_fix_tasks", ["status"], unique=False)

    # 9. delegations (022) — FK → bug_fix_tasks
    op.create_table(
        "delegations",
        sa.Column("task_id", sa.UUID(), nullable=True),
        sa.Column("feat_id", sa.UUID(), nullable=True),
        sa.Column("bug_fix_task_id", sa.UUID(), nullable=True),
        sa.Column("bug_id", sa.UUID(), nullable=True),
        sa.Column("cc_agent", sa.String(length=20), server_default="ubuntu_cc", nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("commit_hash", sa.String(length=40), nullable=True),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("cc_agent IN ('ubuntu_cc')", name="ck_delegations_cc_agent"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed')",
            name="ck_delegations_status",
        ),
        sa.ForeignKeyConstraint(["bug_fix_task_id"], ["bug_fix_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["bug_id"], ["bugs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["feat_id"], ["feats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_delegations_started_at", "delegations", ["started_at"], unique=False)
    op.create_index("ix_delegations_status", "delegations", ["status"], unique=False)
    op.create_index(op.f("ix_delegations_task_id"), "delegations", ["task_id"], unique=False)

    # 10. guardian_reviews (022) — FK → delegations
    op.create_table(
        "guardian_reviews",
        sa.Column("delegation_id", sa.UUID(), nullable=False),
        sa.Column("layer", sa.String(length=10), nullable=False),
        sa.Column("risk_level", sa.String(length=10), nullable=False),
        sa.Column(
            "findings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("passed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "layer IN ('layer1', 'layer2', 'layer3')",
            name="ck_guardian_reviews_layer",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_guardian_reviews_risk_level",
        ),
        sa.ForeignKeyConstraint(["delegation_id"], ["delegations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guardian_reviews_delegation_id", "guardian_reviews", ["delegation_id"], unique=False)
    op.create_index("ix_guardian_reviews_layer", "guardian_reviews", ["layer"], unique=False)
    op.create_index("ix_guardian_reviews_risk_level", "guardian_reviews", ["risk_level"], unique=False)

    # 11. execution_logs (022) — FK → delegations
    op.create_table(
        "execution_logs",
        sa.Column("delegation_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_cost_usd", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("commit_hash", sa.String(length=40), nullable=True),
        sa.Column("commit_verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('done', 'failed')", name="ck_execution_logs_status"),
        sa.ForeignKeyConstraint(["delegation_id"], ["delegations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_execution_logs_delegation_id"), "execution_logs", ["delegation_id"], unique=False)
    op.create_index(op.f("ix_execution_logs_task_id"), "execution_logs", ["task_id"], unique=False)

    # 12. auto_fix_attempts (022) — FK → delegations
    op.create_table(
        "auto_fix_attempts",
        sa.Column("feat_id", sa.UUID(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("error_description", sa.Text(), nullable=False),
        sa.Column("fix_description", sa.Text(), nullable=True),
        sa.Column("delegation_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["delegation_id"], ["delegations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["feat_id"], ["feats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "feat_id",
            "attempt_number",
            name="uq_auto_fix_attempts_feat_id_attempt_number",
        ),
    )
    op.create_index(op.f("ix_auto_fix_attempts_feat_id"), "auto_fix_attempts", ["feat_id"], unique=False)

    # 13. architect_messages (011) — FK → architect_sessions
    op.create_table(
        "architect_messages",
        sa.Column(
            "session_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.String(20),
            nullable=False,
        ),
        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "input_tokens",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "output_tokens",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "cost_usd",
            sa.Numeric(10, 6),
            nullable=True,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["architect_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_architect_messages_role",
        ),
    )
    op.create_index(
        "ix_architect_messages_session_id",
        "architect_messages",
        ["session_id"],
    )

    # 14. professional_spec_chat_messages (035) — FK → professional_specifications
    op.create_table(
        "professional_spec_chat_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("professional_spec_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["professional_spec_id"],
            ["professional_specifications.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_professional_spec_chat_messages_role",
        ),
    )
    op.create_index(
        "ix_professional_spec_chat_messages_professional_spec_id",
        "professional_spec_chat_messages",
        ["professional_spec_id"],
    )

    # 15. ui_design_chat_messages (036) — FK → ui_designs
    op.create_table(
        "ui_design_chat_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ui_design_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ui_design_id"], ["ui_designs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_ui_design_chat_messages_role",
        ),
    )
    op.create_index(
        "ix_ui_design_chat_messages_ui_design_id",
        "ui_design_chat_messages",
        ["ui_design_id"],
    )

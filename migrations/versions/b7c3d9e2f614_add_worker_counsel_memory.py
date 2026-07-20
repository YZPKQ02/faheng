"""add persistent worker counsel memory and simulation handoff

Revision ID: b7c3d9e2f614
Revises: e8f1a4c7d260
Create Date: 2026-07-20 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c3d9e2f614"
down_revision: Union[str, None] = "e8f1a4c7d260"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "worker_counsel_memories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["case_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id"),
    )
    op.create_index(
        "ix_worker_counsel_memories_case_id",
        "worker_counsel_memories",
        ["case_id"],
        unique=True,
    )
    op.create_index(
        "ix_worker_counsel_memories_content_hash",
        "worker_counsel_memories",
        ["content_hash"],
        unique=False,
    )
    with op.batch_alter_table("simulation_sessions") as batch_op:
        batch_op.add_column(
            sa.Column("assistance_mode", sa.String(length=30), server_default="coach", nullable=False)
        )
        batch_op.add_column(
            sa.Column(
                "counsel_agent_id",
                sa.String(length=50),
                server_default="worker_counsel",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("counsel_memory_version", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("counsel_memory_snapshot", sa.JSON(), server_default="{}", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("simulation_sessions") as batch_op:
        batch_op.drop_column("counsel_memory_snapshot")
        batch_op.drop_column("counsel_memory_version")
        batch_op.drop_column("counsel_agent_id")
        batch_op.drop_column("assistance_mode")
    op.drop_index(
        "ix_worker_counsel_memories_content_hash", table_name="worker_counsel_memories"
    )
    op.drop_index("ix_worker_counsel_memories_case_id", table_name="worker_counsel_memories")
    op.drop_table("worker_counsel_memories")

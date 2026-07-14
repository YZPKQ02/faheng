"""add simulation session lifecycle

Revision ID: a92c6f4e1d20
Revises: f36e6e8a91b3
Create Date: 2026-07-14 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a92c6f4e1d20"
down_revision: Union[str, None] = "f36e6e8a91b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("simulation_sessions") as batch_op:
        batch_op.add_column(
            sa.Column("status", sa.String(length=20), server_default="active", nullable=False)
        )
        batch_op.add_column(sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_simulation_sessions_status", ["status"], unique=False)

    op.execute(
        sa.text(
            "UPDATE simulation_sessions "
            "SET created_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
            "WHERE created_at IS NULL OR updated_at IS NULL"
        )
    )

    with op.batch_alter_table("simulation_sessions") as batch_op:
        batch_op.alter_column("created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("simulation_sessions") as batch_op:
        batch_op.drop_index("ix_simulation_sessions_status")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("created_at")
        batch_op.drop_column("status")

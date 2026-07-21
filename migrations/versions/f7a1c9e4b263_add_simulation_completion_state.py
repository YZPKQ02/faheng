"""add simulation completion state

Revision ID: f7a1c9e4b263
Revises: d2f6a9b3c841
Create Date: 2026-07-21 22:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7a1c9e4b263"
down_revision: Union[str, None] = "d2f6a9b3c841"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("simulation_sessions") as batch_op:
        batch_op.add_column(sa.Column("completion_reason", sa.String(30), nullable=True))
        batch_op.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("simulation_sessions") as batch_op:
        batch_op.drop_column("completed_at")
        batch_op.drop_column("completion_reason")

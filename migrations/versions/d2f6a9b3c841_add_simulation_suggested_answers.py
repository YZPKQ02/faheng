"""add worker counsel suggested answers to simulations

Revision ID: d2f6a9b3c841
Revises: c5a8e1d4b729
Create Date: 2026-07-20 01:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2f6a9b3c841"
down_revision: Union[str, None] = "c5a8e1d4b729"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("simulation_sessions") as batch_op:
        batch_op.add_column(
            sa.Column("suggested_answers", sa.JSON(), server_default="[]", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("simulation_sessions") as batch_op:
        batch_op.drop_column("suggested_answers")

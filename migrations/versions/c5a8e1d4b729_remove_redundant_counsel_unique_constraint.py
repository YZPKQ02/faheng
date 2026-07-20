"""remove redundant worker counsel case unique constraint

Revision ID: c5a8e1d4b729
Revises: b7c3d9e2f614
Create Date: 2026-07-20 00:10:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "c5a8e1d4b729"
down_revision: Union[str, None] = "b7c3d9e2f614"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("worker_counsel_memories") as batch_op:
        batch_op.drop_constraint("worker_counsel_memories_case_id_key", type_="unique")


def downgrade() -> None:
    with op.batch_alter_table("worker_counsel_memories") as batch_op:
        batch_op.create_unique_constraint(
            "worker_counsel_memories_case_id_key", ["case_id"]
        )

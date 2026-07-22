"""add analysis publication state

Revision ID: a9c4e7b2d615
Revises: f7a1c9e4b263
Create Date: 2026-07-22 20:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9c4e7b2d615"
down_revision: Union[str, None] = "f7a1c9e4b263"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_conclusions") as batch_op:
        batch_op.add_column(sa.Column("publication_status", sa.String(30), nullable=True))
        batch_op.add_column(sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("published_by", sa.String(200), nullable=True))
    op.execute(
        sa.text(
            "UPDATE analysis_conclusions SET publication_status = 'draft' "
            "WHERE publication_status IS NULL"
        )
    )
    with op.batch_alter_table("analysis_conclusions") as batch_op:
        batch_op.alter_column("publication_status", existing_type=sa.String(30), nullable=False)
        batch_op.create_index(
            "ix_analysis_conclusions_publication_status",
            ["publication_status"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("analysis_conclusions") as batch_op:
        batch_op.drop_index("ix_analysis_conclusions_publication_status")
        batch_op.drop_column("published_by")
        batch_op.drop_column("published_at")
        batch_op.drop_column("publication_status")

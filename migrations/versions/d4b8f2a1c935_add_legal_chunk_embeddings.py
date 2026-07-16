"""add legal chunk embeddings

Revision ID: d4b8f2a1c935
Revises: c0a7d9e6b421
Create Date: 2026-07-16 00:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa


revision: str = "d4b8f2a1c935"
down_revision: Union[str, None] = "c0a7d9e6b421"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    embedding_type = Vector().with_variant(sa.JSON(), "sqlite")
    op.create_table(
        "legal_chunk_embeddings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", embedding_type, nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["legal_chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chunk_id",
            "provider",
            "model",
            name="uq_legal_chunk_embedding_model",
        ),
    )
    op.create_index("ix_legal_chunk_embeddings_chunk_id", "legal_chunk_embeddings", ["chunk_id"])
    op.create_index("ix_legal_chunk_embeddings_provider", "legal_chunk_embeddings", ["provider"])
    op.create_index("ix_legal_chunk_embeddings_model", "legal_chunk_embeddings", ["model"])
    op.create_index("ix_legal_chunk_embeddings_content_hash", "legal_chunk_embeddings", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_legal_chunk_embeddings_content_hash", table_name="legal_chunk_embeddings")
    op.drop_index("ix_legal_chunk_embeddings_model", table_name="legal_chunk_embeddings")
    op.drop_index("ix_legal_chunk_embeddings_provider", table_name="legal_chunk_embeddings")
    op.drop_index("ix_legal_chunk_embeddings_chunk_id", table_name="legal_chunk_embeddings")
    op.drop_table("legal_chunk_embeddings")

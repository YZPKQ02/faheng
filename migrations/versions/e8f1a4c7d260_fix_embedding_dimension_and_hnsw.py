"""fix embedding dimension and add hnsw index

Revision ID: e8f1a4c7d260
Revises: d4b8f2a1c935
Create Date: 2026-07-16 08:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f1a4c7d260"
down_revision: Union[str, None] = "d4b8f2a1c935"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    incompatible = bind.scalar(
        sa.text(
            "SELECT count(*) FROM legal_chunk_embeddings "
            "WHERE vector_dims(embedding) <> 1536"
        )
    )
    if incompatible:
        raise RuntimeError(
            "存在非 1536 维法律向量；请使用目标模型重新索引后再执行迁移"
        )
    op.execute(
        "ALTER TABLE legal_chunk_embeddings "
        "ALTER COLUMN embedding TYPE vector(1536) "
        "USING embedding::vector(1536)"
    )
    op.execute(
        "CREATE INDEX ix_legal_chunk_embeddings_hnsw_cosine "
        "ON legal_chunk_embeddings USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_legal_chunk_embeddings_hnsw_cosine")
    op.execute(
        "ALTER TABLE legal_chunk_embeddings "
        "ALTER COLUMN embedding TYPE vector "
        "USING embedding::vector"
    )

"""add versioned legal knowledge

Revision ID: c0a7d9e6b421
Revises: a92c6f4e1d20
Create Date: 2026-07-16 00:00:00.000000
"""

from datetime import datetime, timezone
import hashlib
import re
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "c0a7d9e6b421"
down_revision: Union[str, None] = "a92c6f4e1d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _canonical_key(title: str) -> str:
    normalized = re.sub(r"\s+", "", title).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def upgrade() -> None:
    op.create_table(
        "legal_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canonical_key", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("authority_type", sa.String(length=50), nullable=False),
        sa.Column("level", sa.String(length=50), nullable=False),
        sa.Column("jurisdiction", sa.String(length=100), nullable=False),
        sa.Column("issuing_body", sa.String(length=200), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_legal_documents_canonical_key", "legal_documents", ["canonical_key"], unique=True)
    op.create_table(
        "legal_document_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("version_label", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("promulgated_on", sa.Date(), nullable=True),
        sa.Column("effective_on", sa.Date(), nullable=False),
        sa.Column("expired_on", sa.Date(), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("review_status", sa.String(length=30), nullable=False),
        sa.Column("supersedes_id", sa.String(length=36), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["legal_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supersedes_id"], ["legal_document_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "content_hash", name="uq_legal_version_document_hash"),
    )
    op.create_index("ix_legal_document_versions_document_id", "legal_document_versions", ["document_id"])
    op.create_index("ix_legal_document_versions_status", "legal_document_versions", ["status"])
    op.create_index("ix_legal_document_versions_effective_on", "legal_document_versions", ["effective_on"])
    op.create_index("ix_legal_document_versions_expired_on", "legal_document_versions", ["expired_on"])
    op.create_index("ix_legal_document_versions_content_hash", "legal_document_versions", ["content_hash"])
    op.create_index("ix_legal_document_versions_review_status", "legal_document_versions", ["review_status"])
    op.create_table(
        "legal_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("authority_id", sa.String(length=36), nullable=True),
        sa.Column("locator", sa.String(length=120), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=300), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["authority_id"], ["legal_authorities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["version_id"], ["legal_document_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "locator", name="uq_legal_chunk_version_locator"),
    )
    op.create_index("ix_legal_chunks_version_id", "legal_chunks", ["version_id"])
    op.create_index("ix_legal_chunks_authority_id", "legal_chunks", ["authority_id"])
    op.create_index("ix_legal_chunks_content_hash", "legal_chunks", ["content_hash"])

    bind = op.get_bind()
    metadata = sa.MetaData()
    authorities = sa.Table("legal_authorities", metadata, autoload_with=bind)
    documents = sa.Table("legal_documents", metadata, autoload_with=bind)
    versions = sa.Table("legal_document_versions", metadata, autoload_with=bind)
    chunks = sa.Table("legal_chunks", metadata, autoload_with=bind)
    now = datetime.now(timezone.utc)
    document_ids: dict[str, str] = {}
    version_ids: dict[tuple[str, object, object], str] = {}
    for row in bind.execute(sa.select(authorities)).mappings():
        key = _canonical_key(row["title"])
        document_id = document_ids.get(key)
        if document_id is None:
            document_id = str(uuid4())
            document_ids[key] = document_id
            bind.execute(
                documents.insert().values(
                    id=document_id,
                    canonical_key=key,
                    title=row["title"],
                    authority_type="statute",
                    level=row["level"],
                    jurisdiction=row["region"],
                    issuing_body=None,
                    source_url=row["source_url"],
                    created_at=now,
                )
            )
        version_key = (document_id, row["effective_on"], row["expired_on"])
        version_id = version_ids.get(version_key)
        if version_id is None:
            version_id = str(uuid4())
            version_ids[version_key] = version_id
            version_hash = hashlib.sha256(
                f"{row['title']}|{row['effective_on']}|{row['expired_on']}".encode("utf-8")
            ).hexdigest()
            bind.execute(
                versions.insert().values(
                    id=version_id,
                    document_id=document_id,
                    version_label="存量数据迁移版本",
                    status="expired" if row["expired_on"] else "active",
                    promulgated_on=None,
                    effective_on=row["effective_on"],
                    expired_on=row["expired_on"],
                    source_url=row["source_url"],
                    content_hash=version_hash,
                    review_status="pending",
                    supersedes_id=None,
                    ingested_at=now,
                )
            )
        content_hash = hashlib.sha256(row["content"].encode("utf-8")).hexdigest()
        bind.execute(
            chunks.insert().values(
                id=str(uuid4()),
                version_id=version_id,
                authority_id=row["id"],
                locator=row["article"],
                sequence=0,
                heading=None,
                content=row["content"],
                keywords=row["keywords"] or [],
                content_hash=content_hash,
            )
        )


def downgrade() -> None:
    op.drop_index("ix_legal_chunks_content_hash", table_name="legal_chunks")
    op.drop_index("ix_legal_chunks_authority_id", table_name="legal_chunks")
    op.drop_index("ix_legal_chunks_version_id", table_name="legal_chunks")
    op.drop_table("legal_chunks")
    op.drop_index("ix_legal_document_versions_review_status", table_name="legal_document_versions")
    op.drop_index("ix_legal_document_versions_content_hash", table_name="legal_document_versions")
    op.drop_index("ix_legal_document_versions_expired_on", table_name="legal_document_versions")
    op.drop_index("ix_legal_document_versions_effective_on", table_name="legal_document_versions")
    op.drop_index("ix_legal_document_versions_status", table_name="legal_document_versions")
    op.drop_index("ix_legal_document_versions_document_id", table_name="legal_document_versions")
    op.drop_table("legal_document_versions")
    op.drop_index("ix_legal_documents_canonical_key", table_name="legal_documents")
    op.drop_table("legal_documents")

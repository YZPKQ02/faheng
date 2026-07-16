"""Vendor-neutral embedding providers and idempotent chunk indexing."""

import hashlib
import math
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.legal_text import tokenize
from app.models import LegalChunk, LegalChunkEmbedding
from app.privacy import redact_sensitive_text


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class DeterministicEmbeddingProvider:
    name = "deterministic"

    def __init__(self, model: str = "legal-hash-v1", dimensions: int = 1536):
        self.model = model
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in tokenize(text):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimensions
                vector[index] += 1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


class HttpEmbeddingProvider:
    name = "http"

    def __init__(self, settings: Settings):
        if not settings.embedding_base_url:
            raise EmbeddingError("EMBEDDING_BASE_URL 未配置")
        self.base_url = settings.embedding_base_url.rstrip("/")
        self.api_key = settings.embedding_api_key
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dimensions
        self.timeout = settings.embedding_timeout_seconds

    def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = httpx.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json={"model": self.model, "input": texts},
                timeout=self.timeout,
            )
            response.raise_for_status()
            rows = sorted(response.json()["data"], key=lambda item: item.get("index", 0))
            vectors = [row["embedding"] for row in rows]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(f"Embedding 接口调用失败：{type(exc).__name__}") from exc
        if len(vectors) != len(texts) or any(
            len(vector) < self.dimensions for vector in vectors
        ):
            raise EmbeddingError("Embedding 返回数量不足或维度低于配置")
        normalized_vectors = []
        for vector in vectors:
            truncated = vector[: self.dimensions]
            norm = math.sqrt(sum(value * value for value in truncated)) or 1.0
            normalized_vectors.append([value / norm for value in truncated])
        return normalized_vectors


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    settings = settings or get_settings()
    if settings.embedding_provider == "http":
        return HttpEmbeddingProvider(settings)
    return DeterministicEmbeddingProvider(
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )


def embed_query(
    provider: EmbeddingProvider,
    query: str,
    *,
    instruction: str | None = None,
) -> list[float]:
    query = redact_sensitive_text(query).text
    if instruction:
        query = f"Instruct: {instruction}\nQuery: {query}"
    return provider.embed([query])[0]


def index_legal_chunks(
    db: Session,
    provider: EmbeddingProvider | None = None,
    *,
    chunk_ids: list[str] | None = None,
    batch_size: int | None = None,
    commit_batches: bool = False,
) -> int:
    provider = provider or get_embedding_provider()
    batch_size = batch_size or get_settings().embedding_batch_size
    if batch_size < 1:
        raise ValueError("batch_size 必须大于 0")
    statement = select(LegalChunk)
    if chunk_ids is not None:
        statement = statement.where(LegalChunk.id.in_(chunk_ids))
    chunks = db.scalars(statement).all()
    pending: list[LegalChunk] = []
    for chunk in chunks:
        existing = db.scalar(
            select(LegalChunkEmbedding).where(
                LegalChunkEmbedding.chunk_id == chunk.id,
                LegalChunkEmbedding.provider == provider.name,
                LegalChunkEmbedding.model == provider.model,
                LegalChunkEmbedding.dimensions == provider.dimensions,
                LegalChunkEmbedding.content_hash == chunk.content_hash,
            )
        )
        if existing:
            continue
        stale = db.scalars(
            select(LegalChunkEmbedding).where(
                LegalChunkEmbedding.chunk_id == chunk.id,
                LegalChunkEmbedding.provider == provider.name,
                LegalChunkEmbedding.model == provider.model,
            )
        ).all()
        for item in stale:
            db.delete(item)
        pending.append(chunk)
    db.flush()

    indexed = 0
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset : offset + batch_size]
        vectors = provider.embed([chunk.content for chunk in batch])
        if len(vectors) != len(batch):
            raise EmbeddingError("Embedding 批次返回数量不一致")
        db.add_all(
            [
                LegalChunkEmbedding(
                    chunk_id=chunk.id,
                    provider=provider.name,
                    model=provider.model,
                    dimensions=provider.dimensions,
                    embedding=vector,
                    content_hash=chunk.content_hash,
                )
                for chunk, vector in zip(batch, vectors, strict=True)
            ]
        )
        db.flush()
        indexed += len(batch)
        if commit_batches:
            db.commit()
    return indexed

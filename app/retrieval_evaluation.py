"""Deterministic metrics for legal retrieval regression tests."""

import json
import math
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.authorities import (
    retrieve_authority_hits,
    retrieve_hybrid_authority_hits,
    retrieve_semantic_authority_hits,
)


class RetrievalEvalCase(BaseModel):
    id: str
    query: str
    expected_citations: list[str] = Field(min_length=1)
    as_of: date | None = None
    region: str = "中国大陆"


def citation_key(title: str, article: str) -> str:
    return f"《{title}》{article}"


def load_retrieval_cases(path: Path) -> list[RetrievalEvalCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [RetrievalEvalCase.model_validate(item) for item in payload]


def evaluate_retrieval(
    db: Session,
    cases: list[RetrievalEvalCase],
    *,
    mode: str = "lexical",
    retriever_options: dict | None = None,
) -> dict:
    retrievers = {
        "lexical": retrieve_authority_hits,
        "semantic": retrieve_semantic_authority_hits,
        "hybrid": retrieve_hybrid_authority_hits,
    }
    if mode not in retrievers:
        raise ValueError(f"不支持的检索模式：{mode}")
    retriever = retrievers[mode]
    retriever_options = retriever_options or {}
    rows = []
    for case in cases:
        hits = retriever(
            db,
            case.query,
            as_of=case.as_of,
            region=case.region,
            limit=10,
            **retriever_options,
        )
        returned = [citation_key(hit.authority.title, hit.authority.article) for hit in hits]
        expected = set(case.expected_citations)
        relevant_ranks = [index for index, key in enumerate(returned, start=1) if key in expected]
        recall_at_5 = len(expected & set(returned[:5])) / len(expected)
        recall_at_10 = len(expected & set(returned[:10])) / len(expected)
        reciprocal_rank = 1 / relevant_ranks[0] if relevant_ranks else 0.0
        dcg = sum(1 / math.log2(rank + 1) for rank in relevant_ranks)
        ideal_dcg = sum(1 / math.log2(rank + 1) for rank in range(1, len(expected) + 1))
        as_of = case.as_of or date.today()
        version_violations = sum(
            1
            for hit in hits
            if hit.authority.effective_on > as_of
            or (hit.authority.expired_on is not None and hit.authority.expired_on <= as_of)
        )
        region_violations = sum(
            1
            for hit in hits
            if hit.authority.region not in ("全国", "中国大陆", case.region)
        )
        rows.append(
            {
                "id": case.id,
                "recall_at_5": recall_at_5,
                "recall_at_10": recall_at_10,
                "mrr": reciprocal_rank,
                "ndcg_at_10": dcg / ideal_dcg if ideal_dcg else 0.0,
                "empty_result": not hits,
                "citation_validity": sum(bool(key) for key in returned) / max(1, len(returned)),
                "version_violations": version_violations,
                "region_violations": region_violations,
                "returned": returned,
            }
        )
    metric_names = ("recall_at_5", "recall_at_10", "mrr", "ndcg_at_10", "citation_validity")
    aggregate = {
        name: round(sum(row[name] for row in rows) / max(1, len(rows)), 4)
        for name in metric_names
    }
    aggregate.update(
        version_violations=sum(row["version_violations"] for row in rows),
        region_violations=sum(row["region_violations"] for row in rows),
        empty_result_rate=round(sum(row["empty_result"] for row in rows) / max(1, len(rows)), 4),
    )
    return {
        "retriever": {
            "lexical": "versioned_lexical_v1",
            "semantic": "semantic_vector_v1",
            "hybrid": "hybrid_rrf_v1",
        }[mode],
        "case_count": len(rows),
        "aggregate": aggregate,
        "cases": rows,
    }

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field


class GoldFact(BaseModel):
    content: str
    status: str = "user_stated"


class GoldEvidence(BaseModel):
    name: str
    purpose: str


class GoldCase(BaseModel):
    id: str
    source_type: str = Field(pattern="^(demonstration|official_model_labeled|lawyer_labeled)$")
    title: str | None = None
    source_url: str | None = None
    source_publisher: str | None = None
    facts: list[GoldFact]
    evidence: list[GoldEvidence] = []
    expected_issues: set[str]
    expected_authority_articles: set[str]
    outcome_supported: bool | None = None
    claims: list[str] = []
    decision: str | None = None
    legal_reasoning: list[str] = []
    annotation_confidence: float | None = Field(default=None, ge=0, le=1)
    review_status: str = "pending_professional_review"


class ConversationEvalTurn(BaseModel):
    """Human-auditable inputs for evaluating one conversation turn."""

    question_focus: str
    question_focus_hit: bool
    follow_up_questions: list[str] = []
    previously_asked_questions: list[str] = []
    answered_questions: list[str] = []
    retrieved_authority_ids: set[str] = set()
    cited_authority_ids: set[str] = set()
    context_fact_conflicts: list[str] = []


@dataclass(frozen=True)
class EvalCase:
    expected_issues: set[str]
    expected_authority_articles: set[str]


def load_gold_cases(path: str | Path) -> list[GoldCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [GoldCase.model_validate(item) for item in payload]


def recall(expected: set[str], actual: set[str]) -> float:
    return 1.0 if not expected else len(expected & actual) / len(expected)


def evaluate_trace(trace: list[dict], authority_articles: dict[str, str], gold: EvalCase) -> dict:
    issues = {item["issue"] for item in trace}
    authority_ids = {aid for item in trace for aid in item["authority_ids"]}
    articles = {authority_articles[aid] for aid in authority_ids if aid in authority_articles}
    elements = [element for item in trace for element in item["elements"]]
    grounded = [element for element in elements if element["fact_ids"] or element["evidence_ids"]]
    return {
        "issue_recall": round(recall(gold.expected_issues, issues), 3),
        "authority_recall": round(recall(gold.expected_authority_articles, articles), 3),
        "grounded_element_rate": round(len(grounded) / max(1, len(elements)), 3),
        "citation_validity": round(len(authority_ids & authority_articles.keys()) / max(1, len(authority_ids)), 3),
    }


def calibration_metrics(samples: list[tuple[float, bool]]) -> dict:
    if not samples:
        return {"sample_count": 0, "brier_score": None, "expected_calibration_error": None}
    brier = sum((prediction - float(outcome)) ** 2 for prediction, outcome in samples) / len(samples)
    bins: dict[int, list[tuple[float, bool]]] = {}
    for prediction, outcome in samples:
        bins.setdefault(min(4, int(prediction * 5)), []).append((prediction, outcome))
    ece = sum(
        len(items) / len(samples)
        * abs(sum(p for p, _ in items) / len(items) - sum(float(y) for _, y in items) / len(items))
        for items in bins.values()
    )
    return {"sample_count": len(samples), "brier_score": round(brier, 4), "expected_calibration_error": round(ece, 4)}


def _normalize_question(value: str) -> str:
    normalized = value.casefold().strip()
    while True:
        stripped = re.sub(r"^\s*(?:\d+\s*[.、．)]\s*)", "", normalized)
        if stripped == normalized:
            break
        normalized = stripped
    return "".join(character for character in normalized if character not in " \t\r\n，。！？?；;：:")


def evaluate_conversation_turn(turn: ConversationEvalTurn) -> dict:
    follow_ups = [_normalize_question(item) for item in turn.follow_up_questions]
    prior = {_normalize_question(item) for item in turn.previously_asked_questions}
    answered = {_normalize_question(item) for item in turn.answered_questions}
    seen: set[str] = set()
    duplicate_count = 0
    answered_repeat_count = 0
    for question in follow_ups:
        if question in seen or question in prior:
            duplicate_count += 1
        if question in answered:
            answered_repeat_count += 1
        seen.add(question)

    unsupported = turn.cited_authority_ids - turn.retrieved_authority_ids
    follow_up_count = len(follow_ups)
    citation_count = len(turn.cited_authority_ids)
    return {
        "question_focus_hit": float(turn.question_focus_hit),
        "follow_up_duplicate_rate": round(duplicate_count / max(1, follow_up_count), 3),
        "answered_information_repeat_rate": round(answered_repeat_count / max(1, follow_up_count), 3),
        "unsupported_authority_rate": round(len(unsupported) / max(1, citation_count), 3),
        "context_fact_conflict_rate": float(bool(turn.context_fact_conflicts)),
        "unsupported_authority_ids": sorted(unsupported),
        "context_fact_conflicts": turn.context_fact_conflicts,
    }


def aggregate_conversation_metrics(turns: list[ConversationEvalTurn]) -> dict:
    metric_names = (
        "question_focus_hit",
        "follow_up_duplicate_rate",
        "answered_information_repeat_rate",
        "unsupported_authority_rate",
        "context_fact_conflict_rate",
    )
    results = [evaluate_conversation_turn(turn) for turn in turns]
    return {
        "turn_count": len(turns),
        **{
            name: round(sum(result[name] for result in results) / max(1, len(results)), 3)
            for name in metric_names
        },
    }

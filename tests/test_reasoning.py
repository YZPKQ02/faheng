from datetime import date

from app.evaluation import EvalCase, calibration_metrics, evaluate_trace, load_gold_cases
from app.models import CaseFile, EvidenceItem, Fact, LegalAuthority
from app.reasoning import (
    build_reasoning_trace,
    calibrated_confidence,
    decision_gate,
    quality_metrics,
    validate_citation_support,
)


def test_trace_links_issue_elements_facts_evidence_and_authority():
    case = CaseFile(id="case", title="违法解除")
    case.facts = [Fact(id="fact", content="公司突然解除劳动合同", status="user_stated")]
    case.evidence = [EvidenceItem(id="evidence", name="解除通知", evidence_type="document", purpose="证明公司解除")]
    authority = LegalAuthority(id="law", title="劳动合同法", article="第八十七条", content="违法解除应支付赔偿金", level="法律", effective_on=date(2008, 1, 1), source_url="https://flk.npc.gov.cn/", keywords=["违法解除"])
    trace = build_reasoning_trace(case, [authority], date(2026, 1, 1))
    assert trace[0]["issue"] == "违法解除"
    assert any(item["evidence_ids"] == ["evidence"] for item in trace[0]["elements"])
    assert trace[0]["authority_ids"] == ["law"]
    metrics = quality_metrics(case, trace)
    assert calibrated_confidence(metrics) <= 0.85


def test_evaluation_is_deterministic_and_checks_valid_citations():
    trace = [{"issue": "违法解除", "authority_ids": ["law"], "elements": [{"fact_ids": ["f"], "evidence_ids": []}]}]
    result = evaluate_trace(trace, {"law": "第八十七条"}, EvalCase({"违法解除"}, {"第八十七条"}))
    assert result == {"issue_recall": 1.0, "authority_recall": 1.0, "grounded_element_rate": 1.0, "citation_validity": 1.0}


def test_citation_support_rejects_unrelated_ids_and_gate_blocks_weak_evidence():
    trace = [{"issue": "加班费", "authority_ids": ["law-overtime"], "elements": []}]
    supported, rejected = validate_citation_support(trace, ["law-overtime", "law-unrelated"])
    assert supported == ["law-overtime"]
    assert rejected == ["law-unrelated"]
    reasons = decision_gate(
        {
            "citation_coverage": 1.0,
            "element_evidence_coverage": 0.2,
            "timeline_conflicts": [],
        },
        supported,
    )
    assert any("证据覆盖" in reason for reason in reasons)


def test_gold_dataset_contract_and_calibration_metrics():
    cases = load_gold_cases("data/evaluation/gold_cases.json")
    assert len(cases) >= 6
    assert all(case.source_type == "demonstration" for case in cases)
    metrics = calibration_metrics([(0.8, True), (0.2, False)])
    assert metrics == {"sample_count": 2, "brier_score": 0.04, "expected_calibration_error": 0.2}


def test_official_model_labeled_dataset_keeps_source_and_review_audit():
    cases = load_gold_cases("data/evaluation/official_model_labeled_cases.json")
    assert len(cases) == 6
    assert all(case.source_type == "official_model_labeled" for case in cases)
    assert all(case.source_url and case.source_publisher for case in cases)
    assert all(case.review_status == "pending_professional_review" for case in cases)
    assert all(case.annotation_confidence is not None for case in cases)

"""Run the deterministic legal reasoning benchmark.

Demonstration cases validate plumbing only. Use lawyer_labeled cases for accuracy claims.
"""

import argparse
import json
from datetime import date
from pathlib import Path

from app.authorities import SEED_AUTHORITIES
from app.evaluation import EvalCase, calibration_metrics, evaluate_trace, load_gold_cases
from app.models import CaseFile, EvidenceItem, Fact, LegalAuthority
from app.reasoning import build_reasoning_trace, calibrated_confidence, quality_metrics


def run(dataset: Path) -> dict:
    gold_cases = load_gold_cases(dataset)
    authorities = [LegalAuthority(id=f"authority-{index}", **item) for index, item in enumerate(SEED_AUTHORITIES)]
    authority_articles = {item.id: item.article for item in authorities}
    results = []
    calibration_samples: list[tuple[float, bool]] = []
    for gold in gold_cases:
        case = CaseFile(id=gold.id, title=gold.id)
        case.facts = [Fact(id=f"{gold.id}-fact-{index}", **item.model_dump()) for index, item in enumerate(gold.facts)]
        case.evidence = [
            EvidenceItem(
                id=f"{gold.id}-evidence-{index}",
                name=item.name,
                purpose=item.purpose,
                evidence_type="benchmark",
            )
            for index, item in enumerate(gold.evidence)
        ]
        trace = build_reasoning_trace(case, authorities, date.today())
        metrics = quality_metrics(case, trace)
        confidence = calibrated_confidence(metrics)
        scores = evaluate_trace(
            trace,
            authority_articles,
            EvalCase(gold.expected_issues, gold.expected_authority_articles),
        )
        if gold.source_type == "lawyer_labeled" and gold.outcome_supported is not None:
            calibration_samples.append((confidence, gold.outcome_supported))
        results.append({"id": gold.id, "source_type": gold.source_type, "confidence": confidence, **scores})
    metric_names = ("issue_recall", "authority_recall", "grounded_element_rate", "citation_validity")
    aggregate = {
        name: round(sum(item[name] for item in results) / max(1, len(results)), 3)
        for name in metric_names
    }
    return {
        "dataset": str(dataset),
        "case_count": len(results),
        "label_warning": (
            "含非律师标注数据；可用于链路评测，不得作为真实案件准确率或概率校准结论"
            if any(item.source_type != "lawyer_labeled" for item in gold_cases)
            else None
        ),
        "aggregate": aggregate,
        "calibration": calibration_metrics(calibration_samples),
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("data/evaluation/gold_cases.json"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.dumps(run(args.dataset), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()

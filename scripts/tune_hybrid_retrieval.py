import argparse
import json
from pathlib import Path

from app.database import SessionLocal
from app.retrieval_evaluation import evaluate_retrieval, load_retrieval_cases


def _score(report: dict) -> tuple[float, float, float]:
    aggregate = report["aggregate"]
    return (
        aggregate["recall_at_10"],
        aggregate["ndcg_at_10"],
        aggregate["mrr"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="仅使用开发集调优混合检索 RRF 权重")
    parser.add_argument(
        "--dev", type=Path, default=Path("data/evaluation/pilot_gold/dev.json")
    )
    parser.add_argument(
        "--test", type=Path, default=Path("data/evaluation/pilot_gold/test.json")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/evaluation/pilot_gold/tuning_report.json"),
    )
    args = parser.parse_args()
    dev_cases = load_retrieval_cases(args.dev)
    trials = []
    with SessionLocal() as db:
        for lexical_weight in (1.0, 1.25, 1.5):
            for semantic_weight in (0.75, 1.0, 1.25):
                options = {
                    "lexical_weight": lexical_weight,
                    "semantic_weight": semantic_weight,
                    "rrf_k": 60,
                }
                report = evaluate_retrieval(
                    db, dev_cases, mode="hybrid", retriever_options=options
                )
                trials.append({"options": options, "aggregate": report["aggregate"]})
        best = max(trials, key=lambda item: _score({"aggregate": item["aggregate"]}))
        baselines = {
            mode: evaluate_retrieval(db, dev_cases, mode=mode)
            for mode in ("lexical", "semantic")
        }
        test_cases = load_retrieval_cases(args.test)
        frozen_test = {
            mode: evaluate_retrieval(db, test_cases, mode=mode)
            for mode in ("lexical", "semantic")
        }
        frozen_test["hybrid"] = evaluate_retrieval(
            db,
            test_cases,
            mode="hybrid",
            retriever_options=best["options"],
        )
    payload = {
        "selection_policy": "maximize dev Recall@10, then nDCG@10, then MRR",
        "dev_case_count": len(dev_cases),
        "test_case_count": len(test_cases),
        "frozen_test_sha256": __import__("hashlib").sha256(args.test.read_bytes()).hexdigest(),
        "best_options": best["options"],
        "dev_trials": trials,
        "dev_baselines": baselines,
        "frozen_test": frozen_test,
        "review_status": "pending",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "best_options": payload["best_options"],
        "dev_best": best["aggregate"],
        "test": {mode: report["aggregate"] for mode, report in frozen_test.items()},
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

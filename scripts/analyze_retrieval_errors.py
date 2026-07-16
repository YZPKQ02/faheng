import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="生成冻结测试集逐题检索错误报告")
    parser.add_argument(
        "--dataset", type=Path, default=Path("data/evaluation/pilot_gold/test.json")
    )
    parser.add_argument(
        "--tuning-report",
        type=Path,
        default=Path("data/evaluation/pilot_gold/tuning_report.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/evaluation/pilot_gold/error_report.json"),
    )
    args = parser.parse_args()
    cases = {
        item["id"]: item
        for item in json.loads(args.dataset.read_text(encoding="utf-8"))
    }
    tuning = json.loads(args.tuning_report.read_text(encoding="utf-8"))
    modes = tuning["frozen_test"]
    report = {
        "dataset": str(args.dataset).replace("\\", "/"),
        "case_count": len(cases),
        "review_status": "pending",
        "modes": {},
    }
    per_mode_rows = {}
    for mode, result in modes.items():
        misses = []
        topic_counts: Counter[str] = Counter()
        difficulty_counts: Counter[str] = Counter()
        per_mode_rows[mode] = {row["id"]: row for row in result["cases"]}
        for row in result["cases"]:
            case = cases[row["id"]]
            expected = set(case["expected_citations"])
            returned = set(row["returned"][:10])
            missing = sorted(expected - returned)
            if not missing:
                continue
            topic_counts[case["topic"]] += 1
            difficulty_counts[case["difficulty"]] += 1
            misses.append(
                {
                    "id": row["id"],
                    "topic": case["topic"],
                    "difficulty": case["difficulty"],
                    "query": case["query"],
                    "missing_citations": missing,
                    "returned": row["returned"],
                    "review_status": "pending",
                }
            )
        report["modes"][mode] = {
            "aggregate": result["aggregate"],
            "case_miss_count": len(misses),
            "misses_by_topic": dict(topic_counts),
            "misses_by_difficulty": dict(difficulty_counts),
            "cases": misses,
        }

    comparisons = Counter()
    for case_id in cases:
        lexical = per_mode_rows["lexical"][case_id]["recall_at_10"]
        hybrid = per_mode_rows["hybrid"][case_id]["recall_at_10"]
        if hybrid > lexical:
            comparisons["hybrid_better"] += 1
        elif hybrid < lexical:
            comparisons["hybrid_worse"] += 1
        else:
            comparisons["equal"] += 1
    report["hybrid_vs_lexical"] = dict(comparisons)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "case_miss_count": {
                    mode: details["case_miss_count"]
                    for mode, details in report["modes"].items()
                },
                "hybrid_vs_lexical": report["hybrid_vs_lexical"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

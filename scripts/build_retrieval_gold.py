import argparse
import hashlib
import json
from pathlib import Path

from app.retrieval_gold import build_gold_candidates, validate_gold_candidates


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="构建待人工审核的法律检索金标准草案")
    parser.add_argument(
        "--corpus", type=Path, default=Path("data/legal/pilot/corpus.jsonl")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/evaluation/pilot_gold")
    )
    args = parser.parse_args()
    candidates = build_gold_candidates(args.corpus)
    report = validate_gold_candidates(candidates, args.corpus)
    serialized = [candidate.model_dump(mode="json") for candidate in candidates]
    _write(args.output_dir / "candidates.json", serialized)
    _write(
        args.output_dir / "dev.json",
        [item for item in serialized if item["split"] == "dev"],
    )
    _write(
        args.output_dir / "test.json",
        [item for item in serialized if item["split"] == "test"],
    )
    _write(args.output_dir / "quality_report.json", report)
    file_hashes = {}
    for name in ("candidates.json", "dev.json", "test.json", "quality_report.json"):
        content = (args.output_dir / name).read_bytes()
        file_hashes[name] = hashlib.sha256(content).hexdigest()
    _write(
        args.output_dir / "dataset_manifest.json",
        {
            "schema_version": "pilot_retrieval_gold_v1",
            "corpus": str(args.corpus).replace("\\", "/"),
            "candidate_count": len(candidates),
            "dev_count": sum(item.split == "dev" for item in candidates),
            "frozen_test_count": sum(item.split == "test" for item in candidates),
            "review_status": "pending",
            "sha256": file_hashes,
        },
    )
    print(json.dumps(report, ensure_ascii=False))
    if report["failed_count"] or report["passed_count"] < 100:
        raise SystemExit("金标准草案未通过质量闸门")


if __name__ == "__main__":
    main()

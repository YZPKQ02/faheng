import argparse
import hashlib
import json
from pathlib import Path

from app.legal_gold_review import review_candidates


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="执行 AI 法律证据审核并生成 reviewed v2")
    parser.add_argument(
        "--candidates",
        type=Path,
        default=Path("data/evaluation/pilot_gold/candidates.json"),
    )
    parser.add_argument(
        "--corpus", type=Path, default=Path("data/legal/pilot/corpus.jsonl")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/evaluation/pilot_gold/reviewed_v2")
    )
    args = parser.parse_args()
    reviewed, report = review_candidates(args.candidates, args.corpus)
    approved = [item for item in reviewed if item["review_disposition"].startswith("approved")]
    rejected = [item for item in reviewed if item["review_disposition"] == "rejected"]
    dev = [item for item in approved if item["split"] == "dev"]
    test = [item for item in approved if item["split"] == "test"]
    _write(args.output_dir / "reviewed_candidates.json", reviewed)
    _write(args.output_dir / "approved.json", approved)
    _write(args.output_dir / "rejected.json", rejected)
    _write(args.output_dir / "dev.json", dev)
    _write(args.output_dir / "test.json", test)
    hashes = {
        name: hashlib.sha256((args.output_dir / name).read_bytes()).hexdigest()
        for name in ("reviewed_candidates.json", "approved.json", "dev.json", "test.json")
    }
    report["sha256"] = hashes
    _write(args.output_dir / "review_report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    if report["approved_count"] < 100 or not report["official_source_check_passed"]:
        raise SystemExit("reviewed v2 未达到发布闸门")


if __name__ == "__main__":
    main()

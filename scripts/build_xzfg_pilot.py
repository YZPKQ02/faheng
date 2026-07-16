import argparse
import hashlib
import json
import time
from pathlib import Path

from app.xzfg_pilot import DETAIL_URL, official_client, parse_law_page, search_law_ids

DEFAULT_QUERIES = ["劳动", "工伤", "社会保险", "工资", "就业"]


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="构建司法部国家行政法规库试点语料")
    parser.add_argument("--output-dir", type=Path, default=Path("data/legal/pilot"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/xzfg"))
    parser.add_argument("--target-min", type=int, default=1000)
    parser.add_argument("--target-max", type=int, default=1500)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--queries", nargs="+", default=DEFAULT_QUERIES)
    args = parser.parse_args()
    if not 0 < args.target_min <= args.target_max:
        raise SystemExit("要求 0 < target-min <= target-max")

    args.raw_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    manifest: list[dict] = []
    failures: list[dict] = []
    candidates: dict[str, list[str]] = {}
    query_counts: dict[str, int] = {}
    accepted_titles: set[str] = set()
    with official_client() as client:
        for query in args.queries:
            law_ids, total = search_law_ids(client, query)
            query_counts[query] = total
            for law_id in law_ids:
                candidates.setdefault(law_id, []).append(query)
            time.sleep(args.delay)
        chunk_count = 0
        for law_id, matched_queries in candidates.items():
            raw_path = args.raw_dir / f"{law_id}.html"
            try:
                if raw_path.exists():
                    html = raw_path.read_text(encoding="utf-8")
                else:
                    response = client.get(DETAIL_URL.format(law_id=law_id))
                    response.raise_for_status()
                    html = response.text
                    raw_path.write_text(html, encoding="utf-8")
                    time.sleep(args.delay)
                document = parse_law_page(html, law_id, keywords=matched_queries)
                normalized_title = "".join(document.title.split())
                if normalized_title in accepted_titles:
                    failures.append(
                        {
                            "law_id": law_id,
                            "title": document.title,
                            "error": "同名法规版本已收录，等待版本沿革人工复核",
                        }
                    )
                    continue
                remaining = args.target_max - chunk_count
                if remaining <= 0:
                    break
                payload = document.model_dump(mode="json")
                if len(payload["chunks"]) > remaining:
                    continue
                records.append(payload)
                accepted_titles.add(normalized_title)
                chunk_count += len(document.chunks)
                manifest.append(
                    {
                        "law_id": law_id,
                        "title": document.title,
                        "source_url": str(document.source_url),
                        "matched_queries": matched_queries,
                        "chunk_count": len(document.chunks),
                        "promulgated_on": document.promulgated_on.isoformat()
                        if document.promulgated_on
                        else None,
                        "effective_on": document.effective_on.isoformat(),
                        "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                        "review_status": "pending",
                    }
                )
                if chunk_count >= args.target_min:
                    break
            except Exception as exc:
                failures.append({"law_id": law_id, "error": str(exc)})

    if chunk_count < args.target_min:
        raise SystemExit(
            f"仅构建 {chunk_count} 个法条块，未达到 target-min={args.target_min}；"
            f"失败 {len(failures)} 部"
        )
    _write_jsonl(args.output_dir / "corpus.jsonl", records)
    report = {
        "source": "国家行政法规库（司法部）",
        "source_url": "https://xzfg.moj.gov.cn/",
        "query_counts": query_counts,
        "document_count": len(records),
        "chunk_count": chunk_count,
        "target_min": args.target_min,
        "target_max": args.target_max,
        "review_status": "pending",
        "documents": manifest,
        "failures": failures,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "source_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {key: report[key] for key in report if key not in {"documents", "failures"}}
    summary["failure_count"] = len(failures)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

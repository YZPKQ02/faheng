import argparse
import hashlib
import json
import time
from pathlib import Path

from app.npc_laws import (
    download_law_docx,
    fetch_law_detail,
    find_active_law,
    official_client,
    parse_npc_law,
)

LAW_SPECS = {
    "中华人民共和国劳动法": ("第一百零七条", 107),
    "中华人民共和国劳动合同法": ("第九十八条", 98),
    "中华人民共和国劳动争议调解仲裁法": ("第五十四条", 54),
    "中华人民共和国社会保险法": ("第九十八条", 98),
    "中华人民共和国就业促进法": ("第六十九条", 69),
    "中华人民共和国工会法": ("第五十八条", 58),
    "中华人民共和国妇女权益保障法": ("第八十六条", 86),
    "中华人民共和国职业病防治法": ("第八十八条", 88),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="构建全国人大劳动相关现行法律语料")
    parser.add_argument("--output-dir", type=Path, default=Path("data/legal/npc_laws_v1"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/npc_laws"))
    parser.add_argument("--delay", type=float, default=0.5)
    args = parser.parse_args()
    args.raw_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    manifest: list[dict] = []
    with official_client() as client:
        for title, (expected_last_locator, expected_article_count) in LAW_SPECS.items():
            metadata_path = args.raw_dir / f"{hashlib.sha256(title.encode()).hexdigest()[:16]}.json"
            if metadata_path.exists():
                detail = json.loads(metadata_path.read_text(encoding="utf-8"))
            else:
                match = find_active_law(client, title)
                detail = fetch_law_detail(client, match["bbbs"])
                metadata_path.write_text(
                    json.dumps(detail, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                time.sleep(args.delay)

            law_id = str(detail["bbbs"])
            docx_path = args.raw_dir / f"{law_id}.docx"
            if docx_path.exists():
                docx = docx_path.read_bytes()
            else:
                docx = download_law_docx(client, law_id)
                docx_path.write_bytes(docx)
                time.sleep(args.delay)

            document = parse_npc_law(
                detail,
                docx,
                expected_title=title,
                expected_last_locator=expected_last_locator,
                expected_article_count=expected_article_count,
            )
            records.append(document.model_dump(mode="json"))
            manifest.append(
                {
                    "law_id": law_id,
                    "title": title,
                    "source_url": str(document.source_url),
                    "status": "有效",
                    "issuing_body": detail["zdjgName"],
                    "promulgated_on": detail["gbrq"],
                    "effective_on": detail["sxrq"],
                    "chunk_count": len(document.chunks),
                    "last_locator": document.chunks[-1].locator,
                    "docx_sha256": hashlib.sha256(docx).hexdigest(),
                    "review_status": "pending",
                }
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    corpus_path = args.output_dir / "corpus.jsonl"
    corpus_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    report = {
        "corpus_version": args.output_dir.name,
        "source": "国家法律法规数据库（全国人大常委会办公厅）",
        "source_url": "https://flk.npc.gov.cn/",
        "document_count": len(records),
        "chunk_count": sum(item["chunk_count"] for item in manifest),
        "review_status": "pending",
        "documents": manifest,
    }
    (args.output_dir / "source_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

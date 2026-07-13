import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.ingestion.pipeline import fetch_public_page, html_to_text, validate_source


def main() -> None:
    parser = argparse.ArgumentParser(description="低频获取单个白名单官方公共 HTML 页面")
    parser.add_argument("url")
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    source_name = validate_source(args.url)
    html = fetch_public_page(args.url)
    text = html_to_text(html)
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"{digest[:16]}.json"
    output.write_text(
        json.dumps(
            {
                "source_url": args.url,
                "source_name": source_name,
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "content_hash": digest,
                "text": text,
                "review_status": "raw_pending_structuring",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()

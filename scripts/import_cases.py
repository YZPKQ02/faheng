import argparse
import json
from pathlib import Path

from app.database import Base, SessionLocal, engine
from app.ingestion.pipeline import import_json_file


def main() -> None:
    parser = argparse.ArgumentParser(description="导入已合法获取并结构化的官方案例 JSON")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        result = import_json_file(db, args.path)
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

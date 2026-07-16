import argparse
import json
from pathlib import Path

from app.database import Base, SessionLocal, engine
from app.legal_ingestion import import_legal_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description="导入已审核来源的版本化法律条文 JSONL")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        result = import_legal_jsonl(db, args.path)
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

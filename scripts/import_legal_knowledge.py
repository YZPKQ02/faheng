import argparse
import json
from pathlib import Path

from app.database import Base, SessionLocal, engine
from app.legal_ingestion import import_legal_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description="导入已审核来源的版本化法律条文 JSONL")
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--transition-manifest",
        type=Path,
        help="与目标语料 SHA-256 绑定的显式版本转换清单",
    )
    args = parser.parse_args()
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        result = import_legal_jsonl(
            db,
            args.path,
            transition_manifest_path=args.transition_manifest,
        )
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

import argparse
import json
from pathlib import Path

from app.authorities import seed_authorities
from app.database import Base, SessionLocal, engine
from app.retrieval_evaluation import evaluate_retrieval, load_retrieval_cases


def main() -> None:
    parser = argparse.ArgumentParser(description="运行法律条文检索基线评测")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/evaluation/legal_retrieval.json"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_authorities(db)
        cases = load_retrieval_cases(args.dataset)
        lexical = evaluate_retrieval(db, cases, mode="lexical")
        semantic = evaluate_retrieval(db, cases, mode="semantic")
        hybrid = evaluate_retrieval(db, cases, mode="hybrid")
        report = {
            "lexical": lexical,
            "semantic": semantic,
            "hybrid": hybrid,
            "hybrid_not_below_lexical": (
                hybrid["aggregate"]["recall_at_10"]
                >= lexical["aggregate"]["recall_at_10"]
            ),
        }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()

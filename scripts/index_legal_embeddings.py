import argparse

from app.database import SessionLocal
from app.embeddings import get_embedding_provider, index_legal_chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="为法律条款生成或更新向量索引")
    parser.parse_args()
    provider = get_embedding_provider()
    with SessionLocal() as db:
        indexed = index_legal_chunks(db, provider, commit_batches=True)
        db.commit()
    print(f"provider={provider.name} model={provider.model} indexed={indexed}")


if __name__ == "__main__":
    main()

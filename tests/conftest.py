import os

os.environ["DATABASE_URL"] = "sqlite:///./test_legal_advisor.db"
os.environ["MODEL_PROVIDER"] = "deterministic"
os.environ["EMBEDDING_PROVIDER"] = "deterministic"
os.environ["EMBEDDING_DIMENSIONS"] = "128"
os.environ["EMBEDDING_CONSENT_REQUIRED"] = "true"
os.environ["DEEPSEEK_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.legal_governance import transition_legal_version
from app.main import app
from app.models import LegalDocumentVersion


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    with TestClient(app) as test_client:
        with SessionLocal() as db:
            versions = list(db.scalars(select(LegalDocumentVersion)).all())
            for version in versions:
                transition_legal_version(
                    db,
                    version,
                    action="approve",
                    actor_id="test-legal-reviewer",
                    roles={"admin"},
                    notes="isolated test corpus",
                )
                transition_legal_version(
                    db,
                    version,
                    action="publish",
                    actor_id="test-legal-publisher",
                    roles={"admin"},
                    notes="isolated test corpus",
                )
            db.commit()
        yield test_client
    Base.metadata.drop_all(bind=engine)

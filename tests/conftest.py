import os

os.environ["DATABASE_URL"] = "sqlite:///./test_legal_advisor.db"
os.environ["MODEL_PROVIDER"] = "deterministic"
os.environ["DEEPSEEK_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    with TestClient(app) as test_client:
        yield test_client
    Base.metadata.drop_all(bind=engine)

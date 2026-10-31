import os

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.db.mongo import ping
from app.db.schema import init_collections


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    try:
        ping()
    except Exception as e:
        # Ngoai CI: skip khi khong co MongoDB. Tren CI skip = xanh gia -> phai fail.
        if os.environ.get("CI") == "true":
            pytest.exit(f"CI requires a reachable MongoDB: {e}", returncode=1)
        pytest.skip(f"MongoDB not reachable, skipping integration tests: {e}")
    init_collections(force=True)


@pytest.fixture
def client():
    return TestClient(app)

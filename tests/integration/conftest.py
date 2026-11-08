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
        # Outside CI: skip when MongoDB is unavailable. On CI a skip is a false green -> must fail.
        if os.environ.get("CI") == "true":
            pytest.exit(f"CI requires a reachable MongoDB: {e}", returncode=1)
        pytest.skip(f"MongoDB not reachable, skipping integration tests: {e}")
    init_collections(force=True)


@pytest.fixture
def client():
    return TestClient(app)

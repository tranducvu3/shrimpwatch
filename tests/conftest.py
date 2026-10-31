import os

import pytest


def pytest_sessionstart(session):
    # Ngoai CI, test_pipeline tu skip khi khong co MongoDB. Tren CI thi skip = xanh gia -> phai fail.
    if os.environ.get("CI") != "true":
        return
    from app.db import get_client

    try:
        get_client().admin.command("ping")
    except Exception as e:
        pytest.exit(f"CI requires a reachable MongoDB: {e}", returncode=1)

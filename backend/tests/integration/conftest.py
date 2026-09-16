"""Integration tests against real Redis and PostgreSQL.

They run only when these optional services are available (they are not part of CI or the default local setup):
    TEST_REDIS_URL=redis://127.0.0.1:6379/15
    TEST_DATABASE_URL=postgresql://<superuser>:<password>@127.0.0.1:5432/postgres
Each test uses a unique Redis key prefix / a throwaway database, so runs don't interfere.
"""

import os
import uuid

import pytest

REDIS_URL = os.environ.get("TEST_REDIS_URL")
DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def redis_url():
    if not REDIS_URL:
        pytest.skip("TEST_REDIS_URL not set")
    return REDIS_URL


@pytest.fixture
def redis_prefix(redis_url):
    import redis

    prefix = f"test-{uuid.uuid4().hex[:10]}"
    yield prefix
    client = redis.Redis.from_url(redis_url)
    for key in client.scan_iter(f"{prefix}:*"):
        client.delete(key)
    client.close()


@pytest.fixture
def database_admin_url():
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    return DATABASE_URL

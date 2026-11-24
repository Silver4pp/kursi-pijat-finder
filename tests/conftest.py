"""Test bootstrap.

Runs before any test module imports the application, so the engine, the upload
directory, and the demo-account switch are already pointed at throwaway values.
"""

import os
import tempfile
from pathlib import Path

import pytest

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="kpf-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_ROOT / 'app.db'}"
os.environ["UPLOAD_DIR"] = str(_TMP_ROOT / "uploads")
os.environ["APP_ENV"] = "test"
os.environ["ALLOW_DEMO_ACCOUNTS"] = "1"
os.environ["COOKIE_SECURE"] = "0"

from app import web  # noqa: E402  (imports after the environment is prepared)
from app.db import Base, SessionLocal, engine, ensure_data_dirs  # noqa: E402
from app.seed import seed_database  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    ensure_data_dirs()
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_database(db)
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Each test starts with empty rate-limit buckets.

    The limiters are process-wide, so without this the login cap is exhausted
    partway through the suite and every later login returns 429.
    """
    _clear()
    yield
    _clear()


def _clear() -> None:
    for limiter in (
        web.login_limiter,
        web.register_limiter,
        web.rating_limiter,
    ):
        limiter._events.clear()

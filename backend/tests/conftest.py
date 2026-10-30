import os
import sys
from datetime import datetime, timezone
from pathlib import Path

TEST_DB = os.environ.get("TEST_DATABASE_URL",
                         "postgresql+psycopg://reception:reception@localhost:5434/reception_test")
# Configure before importing the app: the disposable test database, rules brain, and never a real API key.
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": TEST_DB,
    "GEMINI_API_KEY": "",
    "AGENT_BRAIN": "rules",
    "DEMO_ACCESS_CODE": "",
    "DAILY_MODEL_CALL_LIMIT": "1000",
    "MIN_NOTICE_MINUTES": "60",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, get_engine, reset_engine, session_scope  # noqa: E402

# Monday 2 November 2026, 09:00 in Asia/Kolkata. Every test that books runs against this clock.
FIXED_NOW = datetime(2026, 11, 2, 3, 30, tzinfo=timezone.utc)


def _reachable() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def schema():
    get_settings.cache_clear()
    reset_engine()
    if not _reachable():
        pytest.skip(f"PostgreSQL not reachable at TEST_DATABASE_URL ({TEST_DB.rsplit('@', 1)[-1]}); "
                    "run `docker compose up -d db`")
    from app import models  # noqa: F401
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    reset_engine()


@pytest.fixture()
def database(schema):
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with schema.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    from scripts.seed import seed_reference
    with session_scope() as db:
        seed_reference(db)
    return schema


@pytest.fixture()
def db(database):
    from app.db import SessionLocal
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def rules_engine():
    from app.agent.engine import Engine
    from app.agent.factory import override_engine
    from app.agent.rules import RuleBrain
    engine = Engine(RuleBrain(), clock=lambda: FIXED_NOW)
    override_engine(engine)
    yield engine
    override_engine(None)


@pytest.fixture()
def client(database, rules_engine):
    from fastapi.testclient import TestClient

    from app.api.routes import call_limiter, turn_limiter
    from app.main import create_app

    call_limiter.reset()
    turn_limiter.reset()
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def settings_override(monkeypatch):
    def apply(**values):
        for key, value in values.items():
            monkeypatch.setenv(key.upper(), str(value))
        get_settings.cache_clear()
    yield apply
    get_settings.cache_clear()

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'app.db'}")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# create_all only creates missing tables, so schema changes to existing tables
# are applied here as idempotent migrations tracked in schema_migrations.
SCHEMA_MIGRATIONS = {
    "0001_status_indexes": (
        "CREATE INDEX IF NOT EXISTS ix_change_requests_status ON change_requests (status)",
        "CREATE INDEX IF NOT EXISTS ix_rating_versions_status ON rating_versions (status)",
    ),
}


def run_migrations() -> None:
    """Apply pending schema migrations and record them in schema_migrations."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "name VARCHAR(100) PRIMARY KEY, applied_at VARCHAR(40) NOT NULL)"
            )
        )
        applied = {row[0] for row in conn.execute(text("SELECT name FROM schema_migrations"))}
        for name, statements in SCHEMA_MIGRATIONS.items():
            if name in applied:
                continue
            for statement in statements:
                conn.execute(text(statement))
            conn.execute(
                text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :applied_at)"),
                {"name": name, "applied_at": datetime.now(timezone.utc).isoformat()},
            )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

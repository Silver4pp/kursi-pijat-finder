import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"

# Uploads live next to the database by default. Override with UPLOAD_DIR when the
# app runs from a read-only checkout or in a container with a mounted volume.
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR") or (DATA_DIR / "uploads")).resolve()

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'app.db'}")
IS_SQLITE = DATABASE_URL.startswith("sqlite")
IS_MEMORY_SQLITE = IS_SQLITE and ":memory:" in DATABASE_URL

connect_args = {"check_same_thread": False} if IS_SQLITE else {}
engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

if IS_SQLITE and not IS_MEMORY_SQLITE:

    @event.listens_for(engine, "connect")
    def _apply_sqlite_pragmas(dbapi_connection, _connection_record):
        """Make SQLite usable behind a multi-request ASGI server.

        WAL lets readers proceed while a writer holds the lock, busy_timeout
        turns immediate "database is locked" errors into a short wait, and
        foreign_keys makes the schema's references real instead of decorative.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


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


def ensure_data_dirs() -> None:
    """Create the database and upload directories on demand, not at import time.

    Importing app.db must stay side-effect free so tooling (tests, migrations,
    static analysis) can import the models without touching the filesystem.
    """
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if IS_SQLITE and not IS_MEMORY_SQLITE:
        # Only create the directory that actually holds the configured database,
        # so a test run pointed at a temp path never leaves data/ behind.
        db_parent = Path(DATABASE_URL.split("///", 1)[-1]).resolve().parent
        db_parent.mkdir(parents=True, exist_ok=True)


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
                text(
                    "INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :applied_at)"
                ),
                {"name": name, "applied_at": datetime.now(UTC).isoformat()},
            )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

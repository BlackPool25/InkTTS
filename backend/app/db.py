"""SQLite engine: single file, WAL, FK enforcement, busy timeout.

One database file for the whole app (portable backup-ZIP story; PG deferred).
PRAGMAs applied on every connect via event listener so pool-reused
connections can't silently drop them.
"""
import os
from sqlalchemy import create_engine, event
from sqlmodel import SQLModel

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:////data/db/inkcast.db")
DATA_DIR = os.environ.get("DATA_DIR", "/data")

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False, "timeout": 5}

engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA foreign_keys=ON;")
    cur.execute("PRAGMA busy_timeout=5000;")
    cur.close()


def db_writable() -> bool:
    """Deep-health probe: can we actually write + roll back?"""
    from sqlalchemy import text

    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS _health_probe (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO _health_probe DEFAULT VALUES"))
        conn.execute(text("DELETE FROM _health_probe"))
        conn.commit()
    return True


def create_all() -> None:
    from . import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine)
    _migrate_light()


def _migrate_light() -> None:
    """Column-level catch-up for single-file SQLite without running Alembic.

    Boot uses create_all (fresh DBs get the full schema from metadata), but
    create_all never ALTERs existing tables. For each known post-0001
    column, add it if missing. Alembic revisions remain the source of truth
    for the shape; this is the no-CLI applicator. No-op when current.
    """
    wanted = {"chapters": ["text"]}
    try:
        import sqlite3

        db_path = engine.url.database
        if not db_path or db_path == ":memory:":
            return
        con = sqlite3.connect(db_path, timeout=5)
        try:
            for table, cols in wanted.items():
                have = {r[1] for r in con.execute(
                    f"PRAGMA table_info({table})").fetchall()}
                for col in cols:
                    if col not in have:
                        con.execute(
                            f"ALTER TABLE {table} ADD COLUMN {col} TEXT DEFAULT ''")
            con.commit()
        finally:
            con.close()
    except Exception:
        pass  # health + tests surface real breakage; never crash boot

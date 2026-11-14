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

"""Engine / session management and first-run initialisation."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, Procedure, Setting

SCHEMA_VERSION = "1"

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


def init_db(db_file: Path | str, seed: bool = True) -> Engine:
    """Open (creating if needed) the database and make it the active one."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    db_file = Path(db_file)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_file}", future=True)
    event.listen(engine, "connect", _sqlite_pragmas)
    Base.metadata.create_all(engine)
    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    if seed:
        from .seed import seed_defaults
        with session_scope() as s:
            seed_defaults(s)
    return engine


def close_db() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database not initialised")
    return _engine


def new_session() -> Session:
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised")
    return _SessionLocal()


@contextmanager
def session_scope():
    s = new_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def is_seeded(s: Session) -> bool:
    return s.scalar(select(Procedure.id).limit(1)) is not None


def get_schema_version(s: Session) -> str | None:
    row = s.get(Setting, "schema_version")
    return row.value if row else None

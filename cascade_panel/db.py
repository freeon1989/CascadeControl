"""Сессии SQLite. Один файл базы в каталоге данных панели."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

_engine = None
_Session: sessionmaker[Session] | None = None


def init_db(path: Path) -> None:
    global _engine, _Session
    path.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    with _engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(_engine)
    with _engine.begin() as conn:
        _ensure_column(conn, "routing_rules", "chain_id", "VARCHAR(36)")
        _ensure_column(conn, "routing_rules", "group_id", "VARCHAR(36)")
        _ensure_column(conn, "list_sources", "interval_hours", "INTEGER DEFAULT 24")
        _ensure_column(conn, "list_sources", "selector", "VARCHAR(64) DEFAULT ''")
        _ensure_column(conn, "servers", "protocols_json", "TEXT DEFAULT ''")
        _ensure_column(conn, "clients", "counters_json", "TEXT DEFAULT '{}'")
        _ensure_column(conn, "servers", "hysteria_port", "INTEGER")
        _ensure_column(conn, "servers", "sub_port", "INTEGER")
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    names = {row[1] for row in rows}
    if column not in names:
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def reset_db() -> None:
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None


@contextmanager
def session() -> Iterator[Session]:
    if _Session is None:
        raise RuntimeError("база не инициализирована")
    db = _Session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

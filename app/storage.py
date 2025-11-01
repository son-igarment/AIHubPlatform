"""Persistence helpers for modules logging, Redis locking, and JSONL output."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional
from urllib.parse import urlparse

try:
    import psycopg
except ImportError:  # pragma: no cover - optional dependency
    psycopg = None  # type: ignore

try:
    import redis.asyncio as aioredis
except ImportError:  # pragma: no cover - optional dependency
    aioredis = None  # type: ignore

from .config import ROOT_DIR, settings


logger = logging.getLogger("storage")

_db_driver: Optional[str] = None
_sqlite_path: Optional[Path] = None
_postgres_dsn: Optional[str] = None
_redis_client: Optional["aioredis.Redis"] = None  # type: ignore[name-defined]
_auto_log_path: Path = settings.LOG_DIR / "auto_6h.jsonl"


def init_persistence() -> None:
    """Ensure database tables, Redis client, and log files exist."""
    _configure_database()
    _create_modules_table()
    _initialize_redis()
    _auto_log_path.parent.mkdir(parents=True, exist_ok=True)


def _configure_database() -> None:
    global _db_driver, _sqlite_path, _postgres_dsn
    if _db_driver:
        return
    url = settings.DATABASE_URL or ""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme.startswith("sqlite"):
        if url.startswith("sqlite:///"):
            raw_path = url[len("sqlite:///"):]
        elif url.startswith("sqlite://"):
            raw_path = url[len("sqlite://"):]
        else:
            raw_path = parsed.path
        raw_path = raw_path or "aihub_knowledge.db"
        path = Path(raw_path)
        if not path.is_absolute():
            path = ROOT_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)
        _sqlite_path = path
        _db_driver = "sqlite"
    elif scheme in {"postgres", "postgresql"}:
        if psycopg is None:
            raise RuntimeError("psycopg is required to use a PostgreSQL DATABASE_URL")
        _postgres_dsn = url
        _db_driver = "postgres"
    else:
        raise RuntimeError(f"Unsupported DATABASE_URL scheme: {scheme or 'unknown'}")


def _create_modules_table() -> None:
    if _db_driver == "sqlite":
        assert _sqlite_path is not None
        with sqlite3.connect(_sqlite_path, check_same_thread=False) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS modules_toggles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    actor TEXT,
                    ts INTEGER NOT NULL
                );
                """
            )
            conn.commit()
    elif _db_driver == "postgres":
        assert _postgres_dsn and psycopg is not None
        with psycopg.connect(_postgres_dsn) as conn:  # type: ignore[arg-type]
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS modules_toggles (
                        id SERIAL PRIMARY KEY,
                        module TEXT NOT NULL,
                        enabled BOOLEAN NOT NULL,
                        actor TEXT,
                        ts BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM NOW()))::BIGINT
                    );
                    """
                )
            conn.commit()
    else:
        raise RuntimeError("Database driver not configured; call init_persistence() first")


def _initialize_redis() -> None:
    global _redis_client
    if _redis_client is not None or settings.REDIS_URL in (None, ""):
        return
    if aioredis is None:
        logger.warning("redis package unavailable; automation job lock will use in-process fallback")
        return
    try:
        _redis_client = aioredis.from_url(str(settings.REDIS_URL), decode_responses=True)
    except Exception as exc:  # pragma: no cover - connection error at startup
        logger.warning("Failed to initialize Redis client: %s", exc)
        _redis_client = None


@contextmanager
def _sqlite_connection() -> Generator[sqlite3.Connection, None, None]:
    assert _sqlite_path is not None
    conn = sqlite3.connect(_sqlite_path, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _postgres_connection() -> Generator["psycopg.Connection", None, None]:  # type: ignore[name-defined]
    assert _postgres_dsn and psycopg is not None
    conn = psycopg.connect(_postgres_dsn)  # type: ignore[arg-type]
    try:
        yield conn
    finally:
        conn.close()


def record_module_toggle(module: str, enabled: bool, actor: Optional[str], ts: int) -> None:
    """Persist module toggle event to the configured database."""
    if _db_driver == "sqlite":
        with _sqlite_connection() as conn:
            conn.execute(
                "INSERT INTO modules_toggles (module, enabled, actor, ts) VALUES (?, ?, ?, ?)",
                (module, int(enabled), actor, ts),
            )
            conn.commit()
    elif _db_driver == "postgres":
        with _postgres_connection() as conn:  # type: ignore[arg-type]
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO modules_toggles (module, enabled, actor, ts) VALUES (%s, %s, %s, %s)",
                    (module, enabled, actor, ts),
                )
            conn.commit()
    else:
        logger.warning("record_module_toggle called before database initialization")


async def acquire_job_lock(key: str, ttl: int) -> bool:
    """Attempt to acquire a distributed job lock via Redis; fallback to always true."""
    if _redis_client is None:
        return True
    try:
        return bool(await _redis_client.set(key, str(int(time.time())), ex=ttl, nx=True))  # type: ignore[return-value]
    except Exception as exc:  # pragma: no cover - network issues
        logger.warning("Redis lock acquisition failed (%s); proceeding without distributed lock", exc)
        return True


async def release_job_lock(key: str) -> None:
    if _redis_client is None:
        return
    try:
        await _redis_client.delete(key)
    except Exception as exc:  # pragma: no cover
        logger.debug("Redis lock release failed: %s", exc)


def append_json_log(record: dict) -> None:
    """Append a dictionary as a single-line JSON entry to the automation log file."""
    try:
        line = json.dumps(record, ensure_ascii=False, default=str)
    except Exception as exc:  # pragma: no cover - serialization issues
        logger.warning("Failed to serialize automation log record: %s", exc)
        line = json.dumps({"error": str(exc)})
    _auto_log_path.parent.mkdir(parents=True, exist_ok=True)
    with _auto_log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


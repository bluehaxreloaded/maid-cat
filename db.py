"""Connection to the shared MariaDB database (the same one soap-cat keeps its donors in).

Set DATABASE in constants.py to turn it on. Without it, everything that records to the database is skipped.
Queries run in a worker thread so a slow database never blocks the bot.
"""

import asyncio
import threading
import time
from pathlib import Path

import constants

try:
    import mysql.connector
    from mysql.connector import errors as mysql_errors
except ImportError:  # the bot still runs without it, just without the database
    mysql = None

CONFIG = getattr(constants, "DATABASE", None)
SCHEMA_FILE = Path(__file__).parent / "database.sql"

_lock = threading.Lock()
_conn = None
_schema_ready = False
_schema_lock: asyncio.Lock | None = None  # startup calls arrive together, so only one sets up the schema
_down_until = 0.0  # after a failed connection, skip the database for a bit instead of waiting on it every time
DOWN_SECONDS = 60

# Changes to tables that already exist, applied once each in order (database.sql only creates missing tables).
# Add new ones to the end, never edit or remove one that has shipped.
MIGRATIONS: list[str] = [
    # 1: blocked SOAP and NNID attempts while restricted, so they can be pinged when it ends
    """ALTER TABLE restrictions
       ADD COLUMN soap_attempts INT UNSIGNED NOT NULL DEFAULT 0 AFTER end_reason,
       ADD COLUMN nnid_attempts INT UNSIGNED NOT NULL DEFAULT 0 AFTER soap_attempts""",
    # 2: the helpee's answer to "Does the eShop launch successfully?", so SOAPs that didn't work aren't limited
    """ALTER TABLE transfers
       ADD COLUMN eshop_works TINYINT(1) NULL COMMENT '1 yes, 0 no, NULL not answered' AFTER message_id,
       ADD COLUMN eshop_answered_by BIGINT UNSIGNED NULL AFTER eshop_works,
       ADD COLUMN eshop_answered_at DATETIME NULL AFTER eshop_answered_by""",
    # 3: case notes are kept here instead of case_notes.json, and this says when they go in the topic
    """ALTER TABLE channels
       ADD COLUMN case_setup_complete TINYINT(1) NOT NULL DEFAULT 0
           COMMENT 'step 1 and step 2 are done, so case notes are shown in the topic' AFTER case_notes""",
]


class DatabaseDown(Exception):
    pass


def enabled() -> bool:
    return bool(CONFIG) and mysql is not None


def _connect():
    conn = mysql.connector.connect(
        **CONFIG, autocommit=True, connection_timeout=10, charset="utf8mb4", collation="utf8mb4_unicode_ci"
    )
    cur = conn.cursor()
    cur.execute("SET time_zone = '+00:00'")  # every DATETIME in the database is UTC
    cur.close()
    return conn


def _run(sql: str, params, mode: str, many: bool = False):
    global _conn, _down_until
    if time.monotonic() < _down_until:
        raise DatabaseDown("the database was unreachable a moment ago")
    with _lock:
        for attempt in range(2):
            try:
                if _conn is None or not _conn.is_connected():
                    _conn = _connect()
                cur = _conn.cursor(dictionary=True)
                try:
                    if many:
                        cur.executemany(sql, params)
                    else:
                        cur.execute(sql, params or ())
                    if mode == "all":
                        return cur.fetchall()
                    if mode == "one":
                        row = cur.fetchone()
                        cur.fetchall()  # drain anything left so the connection can be reused
                        return row
                    return cur.lastrowid if mode == "id" else cur.rowcount
                finally:
                    cur.close()
            except (mysql_errors.OperationalError, mysql_errors.InterfaceError):
                # Dropped connection (e.g. the server's idle timeout); reconnect once and try again
                try:
                    if _conn is not None:
                        _conn.close()
                except Exception:
                    pass
                _conn = None
                if attempt:
                    _down_until = time.monotonic() + DOWN_SECONDS
                    raise


async def execute(sql: str, params=None) -> int:
    """Run a statement. Returns the number of rows it changed."""
    return await asyncio.to_thread(_run, sql, params, "count")


async def executemany(sql: str, rows: list) -> int:
    if not rows:
        return 0
    return await asyncio.to_thread(_run, sql, rows, "count", True)


async def insert(sql: str, params=None) -> int:
    """Run an INSERT. Returns the new row's ID."""
    return await asyncio.to_thread(_run, sql, params, "id")


async def fetchone(sql: str, params=None) -> dict | None:
    return await asyncio.to_thread(_run, sql, params, "one")


async def fetchall(sql: str, params=None) -> list[dict]:
    return await asyncio.to_thread(_run, sql, params, "all")


async def ensure_schema():
    """Create any missing tables from database.sql. Safe to run on every start."""
    global _schema_ready, _schema_lock
    if not enabled() or _schema_ready:
        return
    if _schema_lock is None:
        _schema_lock = asyncio.Lock()
    async with _schema_lock:
        if not _schema_ready:
            await _setup_schema()


async def _setup_schema():
    global _schema_ready
    statements = [s.strip() for s in SCHEMA_FILE.read_text(encoding="utf-8").split(";")]
    for statement in statements:
        lines = [l for l in statement.splitlines() if not l.strip().startswith("--")]
        if "".join(lines).strip():
            await execute("\n".join(lines))
    await execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (id INT UNSIGNED PRIMARY KEY, applied_at DATETIME NOT NULL)"
    )
    done = {row["id"] for row in await fetchall("SELECT id FROM schema_migrations")}
    for number, statement in enumerate(MIGRATIONS, 1):
        if number not in done:
            await execute(statement)
            await execute("INSERT INTO schema_migrations (id, applied_at) VALUES (%s, UTC_TIMESTAMP())", (number,))
    _schema_ready = True

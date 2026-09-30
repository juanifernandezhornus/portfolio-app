"""SQLite storage. One connection shared behind a lock (single uvicorn worker)."""
import sqlite3
import threading
import time
from pathlib import Path

from .config import DB_PATH

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  pw_hash TEXT NOT NULL,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolios(
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  public INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS txs(
  id TEXT PRIMARY KEY,
  pid TEXT NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  side TEXT NOT NULL,
  ticker TEXT NOT NULL,
  kind TEXT NOT NULL,
  qty REAL NOT NULL,
  price REAL NOT NULL,
  fx REAL,
  fee REAL NOT NULL DEFAULT 0,
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS txs_user ON txs(user_id);
CREATE INDEX IF NOT EXISTS txs_ticker ON txs(ticker);
-- latest live quote per ticker (shared market data)
CREATE TABLE IF NOT EXISTS assets(
  ticker TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  px REAL,
  chg24 REAL,
  source TEXT,
  updated REAL,
  error TEXT,
  hist_from TEXT,
  hist_try REAL
);
-- daily closes (history + today's live value), shared market data
CREATE TABLE IF NOT EXISTS points(
  ticker TEXT NOT NULL,
  date TEXT NOT NULL,
  px REAL NOT NULL,
  PRIMARY KEY(ticker, date)
);
-- manual prices for assets without a feed, private to each user
CREATE TABLE IF NOT EXISTS manual_points(
  user_id INTEGER NOT NULL,
  ticker TEXT NOT NULL,
  date TEXT NOT NULL,
  px REAL NOT NULL,
  PRIMARY KEY(user_id, ticker, date)
);
"""


def init():
    global _conn
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript(SCHEMA)


def q(sql: str, args=()) -> list[sqlite3.Row]:
    with _lock:
        return _conn.execute(sql, args).fetchall()


def one(sql: str, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def run(sql: str, args=()) -> int:
    with _lock:
        cur = _conn.execute(sql, args)
        return cur.rowcount


def many(sql: str, rows) -> None:
    with _lock:
        _conn.execute("BEGIN")
        try:
            _conn.executemany(sql, rows)
            _conn.execute("COMMIT")
        except Exception:
            _conn.execute("ROLLBACK")
            raise


def purge_sessions():
    run("DELETE FROM sessions WHERE expires < ?", (time.time(),))

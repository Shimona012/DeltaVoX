"""
backend/db.py
SQLite persistence (users, sessions, analysis history).
All queries are parameterised -- never build SQL with string formatting.
"""

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from backend import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id             TEXT PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    display_name   TEXT NOT NULL,
    password_hash  TEXT NOT NULL,
    email_verified INTEGER NOT NULL DEFAULT 0,
    google_sub     TEXT UNIQUE,
    created_at     REAL NOT NULL,
    failed_logins  INTEGER NOT NULL DEFAULT 0,
    locked_until   REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS email_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK (purpose IN ('verify_email', 'reset_password')),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_email_tokens_user_purpose ON email_tokens(user_id, purpose);
CREATE TABLE IF NOT EXISTS analyses (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   REAL NOT NULL,
    overall      REAL NOT NULL,
    flaw_count   INTEGER NOT NULL,
    intent       TEXT NOT NULL,
    ref_label    TEXT NOT NULL,
    eval_label   TEXT NOT NULL,
    eval_sha256  TEXT NOT NULL,
    rubric_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_user ON analyses(user_id, created_at);
"""


def _connect() -> sqlite3.Connection:
    path = config.DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.executescript(SCHEMA)
            # Additive migration for databases created before email verification.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
            if "email_verified" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
            if "google_sub" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN google_sub TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL")
        finally:
            conn.close()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def purge_expired_sessions() -> None:
    with get_conn() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))

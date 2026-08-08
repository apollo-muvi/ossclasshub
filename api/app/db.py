"""ClassHub OSS — SQLite storage layer.

This is the reference storage implementation. The StoragePort interface
(app/storage_port.py) defines the contract that a future Google Sheets
adapter would implement. For now SQLite provides a fast local dev experience.
"""
import sqlite3
import os
import uuid
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Any

from app.config import get_settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def _init_schema(self):
        with self.conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS admins (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS classes (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS students (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                student_code TEXT DEFAULT '',
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS guardians (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                phone TEXT DEFAULT '',
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memberships (
                id TEXT PRIMARY KEY,
                class_id TEXT NOT NULL REFERENCES classes(id),
                student_id TEXT NOT NULL REFERENCES students(id),
                guardian_id TEXT REFERENCES guardians(id),
                role TEXT DEFAULT 'student',
                created_at TEXT NOT NULL,
                UNIQUE(class_id, student_id, guardian_id)
            );

            CREATE TABLE IF NOT EXISTS posts (
                id TEXT PRIMARY KEY,
                class_id TEXT NOT NULL REFERENCES classes(id),
                student_id TEXT REFERENCES students(id),
                category TEXT DEFAULT 'announcement',
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                need_confirm INTEGER DEFAULT 0,
                status TEXT DEFAULT 'published',
                created_at TEXT NOT NULL,
                created_by TEXT DEFAULT 'admin'
            );

            CREATE TABLE IF NOT EXISTS post_images (
                id TEXT PRIMARY KEY,
                post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                file_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'local',
                storage_key TEXT,
                owner_id TEXT DEFAULT '',
                size INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS post_recipients (
                id TEXT PRIMARY KEY,
                post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                student_id TEXT NOT NULL REFERENCES students(id),
                confirmed INTEGER DEFAULT 0,
                confirmed_at TEXT,
                UNIQUE(post_id, student_id)
            );

            CREATE TABLE IF NOT EXISTS post_replies (
                id TEXT PRIMARY KEY,
                post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                student_id TEXT NOT NULL REFERENCES students(id),
                guardian_id TEXT REFERENCES guardians(id),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS parent_invites (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL REFERENCES students(id),
                guardian_name TEXT NOT NULL,
                guardian_phone TEXT DEFAULT '',
                code TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                used INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS parent_sessions (
                token TEXT PRIMARY KEY,
                student_id TEXT NOT NULL REFERENCES students(id),
                guardian_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS parent_notifications (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL REFERENCES students(id),
                post_id TEXT NOT NULL REFERENCES posts(id),
                title TEXT NOT NULL,
                body TEXT DEFAULT '',
                read INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS external_integrations (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                access_token_encrypted TEXT DEFAULT '',
                refresh_token_encrypted TEXT DEFAULT '',
                token_expires_at TEXT DEFAULT '',
                scopes TEXT DEFAULT '',
                external_account_id TEXT DEFAULT '',
                config_json TEXT DEFAULT '{}',
                last_error TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(owner_id, provider)
            );
            """)
            # Migrate existing post_images table — add columns if missing
            cols = {r[1] for r in c.execute("PRAGMA table_info(post_images)").fetchall()}
            if "provider" not in cols:
                c.execute("ALTER TABLE post_images ADD COLUMN provider TEXT NOT NULL DEFAULT 'local'")
            if "storage_key" not in cols:
                c.execute("ALTER TABLE post_images ADD COLUMN storage_key TEXT")
            if "owner_id" not in cols:
                c.execute("ALTER TABLE post_images ADD COLUMN owner_id TEXT DEFAULT ''")

    # --- Generic helpers ---

    def get_setting(self, key: str, default=None):
        with self.conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self.conn() as c:
            c.execute(
                "INSERT INTO settings(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # --- Admins ---

    def get_admin(self, username: str):
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM admins WHERE username=?", (username,)
            ).fetchone()
            return dict(row) if row else None

    def get_first_admin(self):
        with self.conn() as c:
            row = c.execute("SELECT * FROM admins LIMIT 1").fetchone()
            return dict(row) if row else None

    def create_admin(self, username: str, password_hash: str):
        with self.conn() as c:
            c.execute(
                "INSERT INTO admins(username, password_hash, created_at) VALUES(?,?,?)",
                (username, password_hash, _now()),
            )

    def admin_exists(self) -> bool:
        with self.conn() as c:
            return c.execute("SELECT 1 FROM admins LIMIT 1").fetchone() is not None


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        s = get_settings()
        _db = Database(s.db_path)
    return _db

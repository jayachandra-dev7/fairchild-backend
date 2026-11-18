"""Tiny SQLite store for automation settings, run history, dedup log and the rotation cursor.

Operations are a few sub-millisecond statements, so they are called directly (no thread hop).
The database file is ./data/automation.db (on Azure App Service it is /home/data/automation.db, which persists).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from app.core.config import get_settings

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (kind TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger TEXT NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 0,
    local_date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    summary TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS run_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    category_id INTEGER,
    category_name TEXT,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    ref_id INTEGER,
    ref_url TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_items_run ON run_items(run_id);
CREATE TABLE IF NOT EXISTS seen_products (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    wp_product_id INTEGER,
    media_id INTEGER,
    category_id INTEGER,
    name TEXT,
    permalink TEXT,
    image_url TEXT,
    price TEXT,
    sale_price TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_seen_category ON seen_products(category_id);
CREATE TABLE IF NOT EXISTS blog_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wp_post_id INTEGER,
    category_id INTEGER,
    title TEXT,
    link TEXT,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _default_db_path() -> Path:
    # Azure App Service: only /home persists across restarts and redeploys.
    if os.environ.get('WEBSITE_SITE_NAME') and Path('/home').is_dir():
        return Path('/home/data/automation.db')
    return Path(__file__).resolve().parents[2] / 'data' / 'automation.db'


DB_PATH: Path = _default_db_path()


def _db_path() -> Path:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DB_PATH


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = sqlite3.connect(_db_path(), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(SCHEMA)
            cols = {r[1] for r in conn.execute('PRAGMA table_info(runs)')}
            if 'updated_at' not in cols:  # database created by an earlier version
                conn.execute('ALTER TABLE runs ADD COLUMN updated_at TEXT')
            yield conn
            conn.commit()
        finally:
            conn.close()


# ---- settings / state -------------------------------------------------------------------------
def load_settings(kind: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute('SELECT json FROM settings WHERE kind = ?', (kind,)).fetchone()
    return json.loads(row['json']) if row else None


def save_settings(kind: str, data: dict[str, Any]) -> None:
    with _conn() as conn:
        conn.execute(
            'INSERT INTO settings(kind, json) VALUES(?, ?) ON CONFLICT(kind) DO UPDATE SET json = excluded.json',
            (kind, json.dumps(data)),
        )


def get_state(key: str, default: str | None = None) -> str | None:
    with _conn() as conn:
        row = conn.execute('SELECT value FROM state WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else default


def set_state(key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute(
            'INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, value),
        )


# ---- runs -------------------------------------------------------------------------------------
def create_run(*, kind: str, trigger: str, dry_run: bool, local_date: str) -> int:
    with _conn() as conn:
        cur = conn.execute(
            'INSERT INTO runs(kind, status, trigger, dry_run, local_date, started_at) VALUES(?, ?, ?, ?, ?, ?)',
            (kind, 'running', trigger, int(dry_run), local_date, now_iso()),
        )
        return int(cur.lastrowid)


KEEP_RUNS_PER_KIND = 100


def try_create_run(*, kind: str, trigger: str, dry_run: bool, local_date: str) -> int | None:
    """Create a run only if none of this kind is running - one atomic statement, so two workers/instances sharing
    the database can never both start the same job. Returns None when another run is active."""
    now = now_iso()
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO runs(kind, status, trigger, dry_run, local_date, started_at, updated_at) "
            "SELECT ?, 'running', ?, ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM runs WHERE kind = ? AND status = 'running')",
            (kind, trigger, int(dry_run), local_date, now, now, kind),
        )
        return int(cur.lastrowid) if cur.rowcount == 1 else None


def finish_run(run_id: int, status: str, summary: dict[str, Any]) -> None:
    with _conn() as conn:
        conn.execute(
            'UPDATE runs SET status = ?, finished_at = ?, summary = ? WHERE id = ?',
            (status, now_iso(), json.dumps(summary), run_id),
        )
        # Keep the database small: only the most recent runs (and their item logs) are retained.
        conn.execute(
            'DELETE FROM runs WHERE kind = (SELECT kind FROM runs WHERE id = ?) AND id NOT IN '
            '(SELECT id FROM runs WHERE kind = (SELECT kind FROM runs WHERE id = ?) ORDER BY id DESC LIMIT ?)',
            (run_id, run_id, KEEP_RUNS_PER_KIND),
        )
        conn.execute('DELETE FROM run_items WHERE run_id NOT IN (SELECT id FROM runs)')


def update_run_summary(run_id: int, summary: dict[str, Any]) -> None:
    with _conn() as conn:
        conn.execute('UPDATE runs SET summary = ?, updated_at = ? WHERE id = ?', (json.dumps(summary), now_iso(), run_id))


STALE_AFTER_SECONDS = 300


def mark_stale_runs_interrupted(max_age_seconds: int | None = None) -> int:
    """Close 'running' rows whose process is gone. A live run touches its row (summary / log lines) all the time, so a
    row silent for max_age_seconds is dead. None = every running row (tests)."""
    with _conn() as conn:
        if max_age_seconds is None:
            cur = conn.execute("UPDATE runs SET status = 'interrupted', finished_at = ? WHERE status = 'running'", (now_iso(),))
        else:
            cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)).isoformat(timespec='seconds')
            cur = conn.execute(
                "UPDATE runs SET status = 'interrupted', finished_at = ? WHERE status = 'running' AND COALESCE(updated_at, started_at) < ?",
                (now_iso(), cutoff),
            )
        return cur.rowcount


def _run_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data['dry_run'] = bool(data['dry_run'])
    data['summary'] = json.loads(data['summary'] or '{}')
    return data


_FIRST_URL = (
    "(SELECT ref_url FROM run_items WHERE run_id = runs.id AND status = 'created' AND ref_url IS NOT NULL ORDER BY id LIMIT 1)"
)


def list_runs(kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Newest first. Each row also carries first_url: the WordPress link of the first post the run created."""
    with _conn() as conn:
        if kind:
            rows = conn.execute(
                f'SELECT runs.*, {_FIRST_URL} AS first_url FROM runs WHERE kind = ? ORDER BY id DESC LIMIT ?', (kind, limit)
            ).fetchall()
        else:
            rows = conn.execute(f'SELECT runs.*, {_FIRST_URL} AS first_url FROM runs ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return [_run_row(r) for r in rows]


def get_run(run_id: int) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute('SELECT * FROM runs WHERE id = ?', (run_id,)).fetchone()
    return _run_row(row) if row else None


def scheduled_attempts_since(kind: str, since_utc_iso: str) -> list[dict[str, Any]]:
    """Real (non dry-run) scheduled runs of this kind started at/after `since`, oldest first: [{status, started_at}]."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT status, started_at FROM runs WHERE kind = ? AND trigger = 'schedule' AND dry_run = 0 AND started_at >= ? ORDER BY id",
            (kind, since_utc_iso),
        ).fetchall()
    return [dict(r) for r in rows]


def is_kind_running(kind: str) -> bool:
    with _conn() as conn:
        row = conn.execute("SELECT 1 FROM runs WHERE kind = ? AND status = 'running' LIMIT 1", (kind,)).fetchone()
    return row is not None


def add_run_item(
    run_id: int,
    *,
    status: str,
    message: str = '',
    category_id: int | None = None,
    category_name: str | None = None,
    ref_id: int | None = None,
    ref_url: str | None = None,
) -> None:
    with _conn() as conn:
        conn.execute('UPDATE runs SET updated_at = ? WHERE id = ?', (now_iso(), run_id))
        conn.execute(
            'INSERT INTO run_items(run_id, category_id, category_name, status, message, ref_id, ref_url, created_at) '
            'VALUES(?, ?, ?, ?, ?, ?, ?, ?)',
            (run_id, category_id, category_name, status, message[:2000], ref_id, ref_url, now_iso()),
        )


def list_run_items(run_id: int, limit: int = 2000) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute('SELECT * FROM run_items WHERE run_id = ? ORDER BY id LIMIT ?', (run_id, limit)).fetchall()
    return [dict(r) for r in rows]


# ---- dedup / products / blogs -----------------------------------------------------------------
def is_seen(source: str, source_id: str) -> bool:
    with _conn() as conn:
        row = conn.execute('SELECT 1 FROM seen_products WHERE source = ? AND source_id = ?', (source, source_id)).fetchone()
    return row is not None


def seen_ids(source: str, source_ids: list[str]) -> set[str]:
    if not source_ids:
        return set()
    marks = ','.join('?' * len(source_ids))
    with _conn() as conn:
        rows = conn.execute(
            f'SELECT source_id FROM seen_products WHERE source = ? AND source_id IN ({marks})', (source, *source_ids)
        ).fetchall()
    return {r['source_id'] for r in rows}


def record_product(
    *,
    source: str,
    source_id: str,
    wp_product_id: int | None,
    media_id: int | None,
    category_id: int | None,
    name: str,
    permalink: str | None,
    image_url: str | None,
    price: str,
    sale_price: str,
) -> None:
    with _conn() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO seen_products(source, source_id, wp_product_id, media_id, category_id, name, permalink, '
            'image_url, price, sale_price, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (source, source_id, wp_product_id, media_id, category_id, name, permalink, image_url, price, sale_price, now_iso()),
        )


def recent_products(category_ids: list[int], limit: int = 3, days: int = 30) -> list[dict[str, Any]]:
    if not category_ids:
        return []
    marks = ','.join('?' * len(category_ids))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM seen_products WHERE category_id IN ({marks}) AND wp_product_id IS NOT NULL "
            f"AND created_at >= datetime('now', ?) ORDER BY created_at DESC LIMIT ?",
            (*category_ids, f'-{days} days', limit),
        ).fetchall()
    return [dict(r) for r in rows]


def categories_with_products(days: int = 30) -> list[int]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category_id FROM seen_products WHERE category_id IS NOT NULL AND wp_product_id IS NOT NULL "
            "AND created_at >= datetime('now', ?)",
            (f'-{days} days',),
        ).fetchall()
    return [r['category_id'] for r in rows]


def record_blog(*, wp_post_id: int | None, category_id: int | None, title: str, link: str | None) -> None:
    with _conn() as conn:
        conn.execute(
            'INSERT INTO blog_posts(wp_post_id, category_id, title, link, created_at) VALUES(?, ?, ?, ?, ?)',
            (wp_post_id, category_id, title, link, now_iso()),
        )


def blog_categories_since(days: int = 7) -> set[int]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category_id FROM blog_posts WHERE category_id IS NOT NULL AND created_at >= datetime('now', ?)",
            (f'-{days} days',),
        ).fetchall()
    return {r['category_id'] for r in rows}

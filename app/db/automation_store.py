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
    core_id TEXT,
    title_key TEXT,
    image_key TEXT,
    desc_key TEXT,
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
CREATE TABLE IF NOT EXISTS category_guides (
    category_id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    excerpt TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    product_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    link TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_guides_state ON category_guides(state);
CREATE TABLE IF NOT EXISTS calendar_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch TEXT NOT NULL,
    title TEXT NOT NULL,
    keyword TEXT NOT NULL DEFAULT '',
    publish_date TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'planned',
    excerpt TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    wp_post_id INTEGER,
    link TEXT,
    error TEXT,
    scheduled_for TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(title, publish_date)
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
            seen_cols = {r[1] for r in conn.execute('PRAGMA table_info(seen_products)')}
            for col in ('core_id', 'title_key', 'image_key', 'desc_key'):  # added with the duplicate check
                if col not in seen_cols:
                    conn.execute(f'ALTER TABLE seen_products ADD COLUMN {col} TEXT')
            for col in ('core_id', 'title_key', 'image_key', 'desc_key'):
                conn.execute(f'CREATE INDEX IF NOT EXISTS idx_seen_{col} ON seen_products({col})')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_seen_created ON seen_products(created_at)')
            cal_cols = {r[1] for r in conn.execute('PRAGMA table_info(calendar_posts)')}
            if 'publish_time' not in cal_cols:  # per-article publish time (empty = use the time chosen when scheduling)
                conn.execute("ALTER TABLE calendar_posts ADD COLUMN publish_time TEXT NOT NULL DEFAULT ''")
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
KEEP_LOG_LINES = 200  # run log lines kept in total (newest)
KEEP_SEEN_PRODUCTS = 10_000  # distinct products remembered for the duplicate check (newest)
KEEP_PRODUCT_DETAIL_DAYS = 30  # name/link/price are only needed for blogs about recent products; older rows keep keys only


def prune_history(conn: sqlite3.Connection) -> None:
    conn.execute('DELETE FROM run_items WHERE id NOT IN (SELECT id FROM run_items ORDER BY id DESC LIMIT ?)', (KEEP_LOG_LINES,))
    conn.execute(
        'DELETE FROM seen_products WHERE rowid NOT IN (SELECT rowid FROM seen_products ORDER BY created_at DESC, rowid DESC LIMIT ?)',
        (KEEP_SEEN_PRODUCTS,),
    )
    conn.execute(
        "UPDATE seen_products SET name = NULL, permalink = NULL, image_url = NULL, price = NULL, sale_price = NULL, media_id = NULL "
        "WHERE name IS NOT NULL AND created_at < datetime('now', ?)",
        (f'-{KEEP_PRODUCT_DETAIL_DAYS} days',),
    )


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
        prune_history(conn)


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
    core_id: str | None = None,
    title_key: str | None = None,
    image_key: str | None = None,
    desc_key: str | None = None,
    created_at: str | None = None,
) -> None:
    with _conn() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO seen_products(source, source_id, wp_product_id, media_id, category_id, name, permalink, '
            'image_url, price, sale_price, created_at, core_id, title_key, image_key, desc_key) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (source, source_id, wp_product_id, media_id, category_id, name, permalink, image_url, price, sale_price,
             created_at or now_iso(), core_id, title_key, image_key, desc_key),
        )


def seen_fingerprint_keys(keys: list[str]) -> set[str]:
    """Which of these 'core:..' / 'title:..' / 'image:..' keys belong to a product we already posted."""
    wanted: dict[str, list[str]] = {'core': [], 'title': [], 'image': [], 'desc': []}
    for k in keys:
        kind, _, v = k.partition(':')
        if kind in wanted:
            wanted[kind].append(v)
    column = {'core': 'core_id', 'title': 'title_key', 'image': 'image_key', 'desc': 'desc_key'}
    found: set[str] = set()
    with _conn() as conn:
        for kind, values in wanted.items():
            values = list(dict.fromkeys(values))
            for i in range(0, len(values), 400):
                chunk = values[i : i + 400]
                marks = ','.join('?' * len(chunk))
                for r in conn.execute(f'SELECT DISTINCT {column[kind]} AS v FROM seen_products WHERE {column[kind]} IN ({marks})', chunk):
                    found.add(f'{kind}:{r["v"]}')
    return found


def fill_keys_for_wp_id(wp_id: int, core_id: str | None, title_key: str | None) -> bool:
    """True when a row for this WordPress product already exists (missing keys are filled in, e.g. rows saved before
    the duplicate check existed); False when the product is new to us."""
    with _conn() as conn:
        row = conn.execute('SELECT 1 FROM seen_products WHERE wp_product_id = ? LIMIT 1', (wp_id,)).fetchone()
        if row is None:
            return False
        conn.execute(
            'UPDATE seen_products SET core_id = COALESCE(core_id, ?), title_key = COALESCE(title_key, ?) WHERE wp_product_id = ?',
            (core_id, title_key, wp_id),
        )
        return True


def count_wp_seeds() -> int:
    with _conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM seen_products WHERE source = 'wp'").fetchone()[0])


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


# ---- content calendar (planned posts, reviewed in the app, then scheduled in WordPress) -------------
CALENDAR_FIELDS = ('title', 'keyword', 'publish_date', 'publish_time', 'state', 'excerpt', 'content', 'wp_post_id', 'link', 'error', 'scheduled_for')


def calendar_add_many(batch: str, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """Insert plan rows; a row with the same title and date is skipped. Returns (added, skipped)."""
    added = skipped = 0
    now = now_iso()
    with _conn() as conn:
        for r in rows:
            cur = conn.execute(
                'INSERT OR IGNORE INTO calendar_posts(batch, title, keyword, publish_date, state, created_at, updated_at) '
                "VALUES(?, ?, ?, ?, 'planned', ?, ?)",
                (batch, r['title'].strip(), str(r.get('keyword') or '').strip(), r['publish_date'], now, now),
            )
            if cur.rowcount:
                added += 1
            else:
                skipped += 1
    return added, skipped


def calendar_list(batch: str | None = None) -> list[dict[str, Any]]:
    with _conn() as conn:
        if batch:
            rows = conn.execute('SELECT * FROM calendar_posts WHERE batch = ? ORDER BY publish_date, id', (batch,)).fetchall()
        else:
            rows = conn.execute('SELECT * FROM calendar_posts ORDER BY publish_date, id').fetchall()
    return [dict(r) for r in rows]


def calendar_get(post_id: int) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute('SELECT * FROM calendar_posts WHERE id = ?', (post_id,)).fetchone()
    return dict(row) if row else None


def calendar_update(post_id: int, **fields: Any) -> dict[str, Any] | None:
    cols = {k: v for k, v in fields.items() if k in CALENDAR_FIELDS}
    if cols:
        sets = ', '.join(f'{k} = ?' for k in cols)
        with _conn() as conn:
            conn.execute(f'UPDATE calendar_posts SET {sets}, updated_at = ? WHERE id = ?', (*cols.values(), now_iso(), post_id))
    return calendar_get(post_id)


def calendar_delete(post_id: int) -> None:
    with _conn() as conn:
        conn.execute('DELETE FROM calendar_posts WHERE id = ?', (post_id,))


def calendar_reset_stuck() -> int:
    """Rows left in 'generating' by a restart go back to 'planned' so they can be generated again."""
    with _conn() as conn:
        return conn.execute("UPDATE calendar_posts SET state = 'planned', updated_at = ? WHERE state = 'generating'", (now_iso(),)).rowcount


def calendar_delete_batch(batch: str) -> int:
    """Remove a whole uploaded plan (its rows and saved guidelines) from the app. WordPress posts are not touched."""
    with _conn() as conn:
        n = conn.execute('DELETE FROM calendar_posts WHERE batch = ?', (batch,)).rowcount
        conn.execute('DELETE FROM state WHERE key = ?', (f'calendar_guidelines:{batch}',))
    return n


# ---- category guides (one shopping guide per end-point category, written into the WooCommerce category description) --
GUIDE_FIELDS = ('state', 'excerpt', 'content', 'product_count', 'error', 'link', 'published_at')


def guide_upsert(category_id: int, path: str, **fields: Any) -> dict[str, Any]:
    cols = {k: v for k, v in fields.items() if k in GUIDE_FIELDS}
    now = now_iso()
    with _conn() as conn:
        if conn.execute('SELECT 1 FROM category_guides WHERE category_id = ?', (category_id,)).fetchone():
            if cols:
                sets = ', '.join(f'{k} = ?' for k in cols)
                conn.execute(f'UPDATE category_guides SET {sets}, path = ?, updated_at = ? WHERE category_id = ?', (*cols.values(), path, now, category_id))
        else:
            data = {'state': 'queued', **cols}
            names = ', '.join(data)
            conn.execute(
                f'INSERT INTO category_guides(category_id, path, {names}, created_at, updated_at) VALUES(?, ?, {", ".join("?" * len(data))}, ?, ?)',
                (category_id, path, *data.values(), now, now),
            )
    return guide_get(category_id) or {}


def guide_get(category_id: int) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute('SELECT * FROM category_guides WHERE category_id = ?', (category_id,)).fetchone()
    return dict(row) if row else None


def guide_ids() -> set[int]:
    with _conn() as conn:
        return {r[0] for r in conn.execute('SELECT category_id FROM category_guides')}


def guide_list(state: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    where, args = [], []
    if state:
        where.append('state = ?')
        args.append(state)
    if q:
        where.append('path LIKE ?')
        args.append(f'%{q}%')
    sql = 'SELECT category_id, path, state, excerpt, product_count, error, link, updated_at, published_at, length(content) > 0 AS has_content FROM category_guides'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY updated_at DESC LIMIT ? OFFSET ?'
    with _conn() as conn:
        rows = conn.execute(sql, (*args, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def guide_counts() -> dict[str, int]:
    with _conn() as conn:
        return {r['state']: r['n'] for r in conn.execute('SELECT state, COUNT(*) AS n FROM category_guides GROUP BY state')}


def guide_ids_in_state(state: str, limit: int) -> list[int]:
    with _conn() as conn:
        return [r[0] for r in conn.execute('SELECT category_id FROM category_guides WHERE state = ? ORDER BY updated_at LIMIT ?', (state, limit))]


def guide_reset_stuck() -> int:
    with _conn() as conn:
        return conn.execute("UPDATE category_guides SET state = 'queued', updated_at = ? WHERE state = 'generating'", (now_iso(),)).rowcount

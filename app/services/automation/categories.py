"""WordPress product-category snapshot.

The full tree (thousands of Google-taxonomy categories) is pulled from WooCommerce once and stored in
app/data/wp_categories.json, so daily jobs never page through the WordPress API. If an id turns out to be
stale (WordPress rejects it), callers run `sync_snapshot()` once and re-resolve.
"""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.db import automation_store
from app.services.platform_auth_service import platform_auth_service
from app.services.wordpress.service import WordPressService

logger = logging.getLogger(__name__)

SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / 'data' / 'wp_categories.json'
_cache: dict[str, Any] | None = None


def runtime_path() -> Path:
    """Refreshed copy of the snapshot, kept next to the database (persistent on Azure; the deployed app folder may be
    read-only). It takes precedence over the snapshot bundled in the repo."""
    return automation_store.DB_PATH.parent / 'wp_categories.json'


def build_snapshot(raw_categories: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn the raw WooCommerce category list into a compact tree description."""
    by_id: dict[int, dict[str, Any]] = {int(c['id']): c for c in raw_categories}
    parents = {int(c.get('parent') or 0) for c in raw_categories}

    def lineage(cat_id: int) -> list[int]:
        chain: list[int] = []
        seen: set[int] = set()
        current: int = cat_id
        while current and current in by_id and current not in seen:
            seen.add(current)
            chain.append(current)
            current = int(by_id[current].get('parent') or 0)
        return list(reversed(chain))

    items: list[dict[str, Any]] = []
    for cat_id, cat in sorted(by_id.items()):
        path_ids = lineage(cat_id)
        items.append(
            {
                'id': cat_id,
                'name': html.unescape(str(cat.get('name', ''))).strip(),
                'slug': cat.get('slug', ''),
                'parent': int(cat.get('parent') or 0),
                'depth': len(path_ids),
                'is_leaf': cat_id not in parents,
                'path': ' > '.join(html.unescape(str(by_id[i].get('name', ''))).strip() for i in path_ids),
                'path_ids': path_ids,
            }
        )
    return {
        'synced_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'total': len(items),
        'leaf_count': sum(1 for i in items if i['is_leaf']),
        'categories': items,
    }


def load_snapshot(*, force: bool = False) -> dict[str, Any]:
    global _cache
    if _cache is not None and not force:
        return _cache
    path = runtime_path() if runtime_path().exists() else SNAPSHOT_PATH
    if not path.exists():
        raise FileNotFoundError(
            f'{SNAPSHOT_PATH} not found. Run `python -m scripts.sync_wp_categories` or POST /api/v1/automation/categories/sync.'
        )
    _cache = json.loads(path.read_text(encoding='utf-8'))
    return _cache


def all_categories() -> list[dict[str, Any]]:
    return load_snapshot()['categories']


def leaf_categories(exclude_ids: set[int] | None = None) -> list[dict[str, Any]]:
    skip = exclude_ids or set()
    return [
        c
        for c in all_categories()
        if c['is_leaf'] and c['id'] not in skip and c['name'].strip().lower() != 'uncategorized'
    ]


def category_by_id(cat_id: int) -> dict[str, Any] | None:
    return next((c for c in all_categories() if c['id'] == cat_id), None)


def find_by_path(path: str) -> dict[str, Any] | None:
    wanted = path.strip().lower()
    return next((c for c in all_categories() if c['path'].lower() == wanted), None)


async def _fetch_all_raw(*, domain: str, key: str, secret: str) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    page = 1
    base = WordPressService._normalize_domain(domain)  # noqa: SLF001
    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            resp = await client.get(
                f'{base}/wp-json/wc/v3/products/categories',
                params={'per_page': 100, 'page': page, 'hide_empty': 'false'},
                auth=(key, secret),
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            raw.extend(batch)
            total_pages = int(resp.headers.get('x-wp-totalpages', page))
            if page >= total_pages:
                break
            page += 1
    return raw


async def sync_snapshot(*, to_repo: bool = False) -> dict[str, Any]:
    """Pull the whole category tree from WordPress and rewrite the JSON snapshot (in the persistent data folder, or
    in the repo's bundled file when `to_repo` - used by the developer script)."""
    global _cache
    creds = platform_auth_service.get_credentials('wordpress')
    if not creds:
        raise RuntimeError('WordPress credentials are not configured.')
    raw = await _fetch_all_raw(
        domain=creds['domain'], key=creds['wc_consumer_key'], secret=creds['wc_consumer_secret']
    )
    snapshot = build_snapshot(raw)
    target = SNAPSHOT_PATH if to_repo else runtime_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    tmp.replace(target)
    _cache = snapshot
    logger.info('wp_categories_synced', extra={'total': snapshot['total'], 'leaves': snapshot['leaf_count']})
    return snapshot

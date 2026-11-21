"""Teach the duplicate check about products already on WordPress.

On Azure the SQLite file starts empty, and products can also be created by hand or by the old poster. Before each run
the newest WordPress products are read and only their fingerprints (a few short keys) are stored, never the content.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.db import automation_store
from app.services.automation import dedupe
from app.services.platform_auth_service import platform_auth_service

PAGE = 100
FIRST_RUN_PAGES = 100  # up to 10,000 products when nothing has been learned yet
LATER_PAGES = 10  # afterwards only the newest products matter; stops early at the first page that is all known


def _meta(product: dict[str, Any], key: str) -> str:
    for m in product.get('meta_data') or []:
        if m.get('key') == key:
            return str(m.get('value') or '')
    return ''


async def seed(run: Any) -> int:
    creds = platform_auth_service.get_credentials('wordpress')
    if not creds:
        return 0
    base = str(creds['domain']).strip().rstrip('/')
    if not base.startswith('http'):
        base = f'https://{base}'
    max_pages = FIRST_RUN_PAGES if automation_store.count_wp_seeds() == 0 else LATER_PAGES
    added = 0
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            for page in range(1, max_pages + 1):
                resp = await client.get(
                    f'{base}/wp-json/wc/v3/products',
                    params={'per_page': PAGE, 'page': page, 'orderby': 'id', 'order': 'desc', 'status': 'any',
                            '_fields': 'id,name,date_created_gmt,meta_data'},
                    auth=(creds['wc_consumer_key'], creds['wc_consumer_secret']),
                )
                resp.raise_for_status()
                products = resp.json()
                if not products:
                    break
                fresh = []
                for p in products:
                    src, sid = _meta(p, 'automation_source'), _meta(p, 'automation_source_id')
                    core = dedupe.core_id(src, sid) if src and sid else None
                    tkey = dedupe.title_key(str(p.get('name') or ''))
                    if automation_store.fill_keys_for_wp_id(int(p['id']), core, tkey):
                        continue
                    fresh.append(p)
                    created = str(p.get('date_created_gmt') or '')
                    automation_store.record_product(
                        source='wp', source_id=str(p['id']), wp_product_id=int(p['id']), media_id=None, category_id=None,
                        name=None, permalink=None, image_url=None, price=None, sale_price=None,  # type: ignore[arg-type]
                        core_id=core, title_key=tkey, created_at=(created + '+00:00') if created else None,
                    )
                added += len(fresh)
                if not fresh:  # everything on this page is already known, older pages are too
                    break
                if len(products) < PAGE:
                    break
    except Exception as exc:  # noqa: BLE001 - the local list is still used
        run.log('warn', f'Could not read existing WordPress products for the duplicate check ({exc.__class__.__name__}); using local history only.')
        return added
    if added:
        run.log('info', f'Duplicate check learned {added} product(s) already on WordPress.')
    return added

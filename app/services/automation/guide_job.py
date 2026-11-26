"""Category guides: one shopping-helper guide per end-point category, written into the WooCommerce category description.

Products come from the CJ / Impact feeds (department stores the user picked), never from scraping store pages. A category
with too few real products is skipped instead of getting a thin page. Drafting and publishing are separate steps so a
person can review a sample before the daily drip is allowed to publish on its own.
"""
from __future__ import annotations

import asyncio
import logging
import re
import statistics
from typing import Any

import httpx

from app.db import automation_store
from app.schemas.automation import Candidate, GuideAutomationSettings, ProductAutomationSettings
from app.services.automation import categories, claude_review, config_store, dedupe, filters, links, sourcing
from app.services.platform_auth_service import platform_auth_service
from app.services.wordpress.service import WordPressService

logger = logging.getLogger(__name__)

MIN_PRODUCTS = 3
MAX_FEATURED = 5
MAX_PAGES = 3
MARKER = 'gpc-category-guide'  # our wrapper class: only descriptions that carry it may be overwritten
DISCLOSURE = (
    '<p><em>Disclosure: some links on this page are affiliate links. If you buy through them we may earn a small '
    'commission at no extra cost to you.</em></p>'
)
SYSTEM = (
    'You write helpful, honest shopping guides for a US deals website. Use ONLY the product data and URLs provided. '
    'Never invent specs, ratings, materials, sizes, brand claims or URLs, and never promise a price will last. '
    'Respond with a single JSON object and nothing else.'
)

_busy = False  # one drafting batch at a time


def is_busy() -> bool:
    return _busy


# ---------------------------------------------------------------- picking categories
async def pending_categories(count: int) -> list[dict[str, Any]]:
    """Next end-point categories without a guide: those that already have products in the store first."""
    done = automation_store.guide_ids()
    store = {c['id']: c['count'] for c in await links.store_categories() if c.get('id') is not None}
    todo = [c for c in categories.leaf_categories() if c['id'] not in done and c['name'].lower() != 'uncategorized']
    todo.sort(key=lambda c: (-store.get(c['id'], 0), c['path']))
    return todo[:count]


# ---------------------------------------------------------------- drafting
def _product_cfg(g: GuideAutomationSettings) -> ProductAutomationSettings:
    shared = config_store.load_all().product  # the shared "ignore keywords" list also protects guides
    return ProductAutomationSettings(
        use_cj=g.use_cj, use_impact=g.use_impact, cj_advertiser_ids=g.cj_advertiser_ids, impact_campaign_ids=g.impact_campaign_ids,
        min_discount=0, min_price=g.min_price, max_price=g.max_price, ignore_keywords=shared.ignore_keywords,
    )


async def gather_products(leaf: dict[str, Any], g: GuideAutomationSettings) -> list[Candidate]:
    """Real, in-stock, relevant products for the category (up to a few feed pages)."""
    cfg = _product_cfg(g)
    ignore = [w for w in (cfg.ignore_keywords or '').lower().replace('\n', ',').split(',') if w.strip()]
    ignore = [w.strip() for w in ignore]
    state: dict[str, Any] = {}
    kept: list[Candidate] = []
    taken: set[str] = set()
    for _ in range(MAX_PAGES):
        batch, _errors = await sourcing.fetch_candidates(leaf, cfg, state=state)
        if not batch:
            break
        for c in batch:
            if filters.reject_reason(c, cfg, ignore):
                continue
            keys = dedupe.fingerprint(c).keys()
            if taken.intersection(keys):
                continue
            taken.update(keys)
            kept.append(c)
        if len(kept) >= MAX_FEATURED * 2:
            break
    kept.sort(key=lambda c: c.relevance, reverse=True)
    return kept


def _add_rel(html: str, urls: set[str]) -> str:
    """Affiliate links get rel="sponsored nofollow noopener" and open in a new tab."""

    def fix(m: re.Match[str]) -> str:
        attrs = m.group(1)
        href = re.search(r'href\s*=\s*(["\'])(.*?)\1', attrs, re.I | re.S)
        if not href or href.group(2) not in urls or re.search(r'\brel\s*=', attrs, re.I):
            return m.group(0)
        return f'<a{attrs} rel="sponsored nofollow noopener" target="_blank">'

    return re.sub(r'<a\b([^>]*)>', fix, html)


def build_prompt(leaf: dict[str, Any], products: list[Candidate], hint: str) -> str:
    prices = [p.sale_price for p in products]
    items = [
        {'name': p.title[:120], 'store': p.advertiser or p.brand, 'price': round(p.sale_price, 2), 'was': round(p.price, 2), 'url': p.buy_url}
        for p in products[:MAX_FEATURED]
    ]
    return (
        f'Category page: "{leaf["path"]}"\n'
        f'Deals found right now: {len(products)}. Price range: ${min(prices):.0f} to ${max(prices):.0f} (typical about ${statistics.median(prices):.0f}).\n'
        f'Featured products (link each as <a href="URL">name</a>; use only these names, prices and URLs):\n{items}\n\n{hint}\n'
        'Write a shopping helper guide of 400-600 words as clean HTML (<h2>, <p>, <ul>, <a>; no <html>/<body>, no <h1>, no scripts) with: '
        'a short intro on who this category suits, "How to choose" with 3-4 practical points, "What to look for", a "Deals to check out" list of the featured products with their price, '
        'and a short closing tip. Mention the price range. Do not claim anything about quality, materials or sizes that is not in the data. '
        'The excerpt is a plain-text summary of 140-155 characters. Return exactly:\n{"excerpt":"...","content":"<p>...</p>"}'
    )


async def generate_guide(category_id: int, g: GuideAutomationSettings) -> dict[str, Any]:
    leaf = categories.category_by_id(category_id)
    if not leaf:
        return automation_store.guide_upsert(category_id, f'#{category_id}', state='error', error='Unknown category')
    path = leaf['path']
    try:
        products = await gather_products(leaf, g)
        if len(products) < MIN_PRODUCTS:
            return automation_store.guide_upsert(category_id, path, state='skipped', product_count=len(products), content='', error=f'Not enough products ({len(products)}, need {MIN_PRODUCTS}).')
        hint = await links.category_hint(path, limit=10)
        raw = await claude_review.ask_claude(build_prompt(leaf, products, hint), max_tokens=3000, temperature=0.6, system=SYSTEM)
        data = claude_review.extract_json(raw)
        body = str(data.get('content') or '').strip()
        if not body:
            raise ValueError('Claude returned an empty guide.')
        urls = {p.buy_url for p in products}
        body = await links.clean_html(body, allowed=urls)
        body = _add_rel(body, urls)
        content = f'<div class="{MARKER}">{body}{DISCLOSURE}</div>'
        return automation_store.guide_upsert(
            category_id, path, state='generated', excerpt=str(data.get('excerpt') or '').strip()[:300], content=content,
            product_count=len(products), error=None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning('guide draft failed for %s: %s', category_id, exc)
        detail = f'{exc.response.status_code} {exc.response.text[:200]}' if isinstance(exc, httpx.HTTPStatusError) else f'{exc.__class__.__name__}: {str(exc)[:200]}'
        return automation_store.guide_upsert(category_id, path, state='error', error=detail)


async def _draft_batch(ids: list[int], g: GuideAutomationSettings) -> None:
    global _busy
    try:
        for cid in ids:
            await generate_guide(cid, g)
    finally:
        _busy = False


async def start_drafting(ids: list[int], g: GuideAutomationSettings) -> int:
    """Queue the categories and draft them one after another in the background. Returns how many were queued."""
    global _busy
    if _busy:
        raise RuntimeError('Drafting is already running. Wait for it to finish.')
    queued = 0
    for cid in ids:
        leaf = categories.category_by_id(cid)
        row = automation_store.guide_get(cid)
        if not leaf or (row and row['state'] in ('generating', 'published')):
            continue
        automation_store.guide_upsert(cid, leaf['path'], state='generating', error=None)
        queued += 1
    if queued:
        _busy = True
        asyncio.get_running_loop().create_task(_draft_batch([i for i in ids if (automation_store.guide_get(i) or {}).get('state') == 'generating'], g))
    return queued


# ---------------------------------------------------------------- publishing
def _wp_kw() -> dict[str, Any]:
    creds = platform_auth_service.get_credentials('wordpress')
    if not creds:
        raise RuntimeError('WordPress credentials are not configured.')
    return {'domain': creds['domain'], 'wc_consumer_key': creds['wc_consumer_key'], 'wc_consumer_secret': creds['wc_consumer_secret']}


async def publish_guide(category_id: int) -> dict[str, Any]:
    """Write the guide into the category description. A description someone wrote by hand is never overwritten."""
    row = automation_store.guide_get(category_id)
    if not row or row['state'] not in ('generated', 'approved', 'published') or not row['content']:
        raise ValueError('Draft and approve the guide first.')
    kw = _wp_kw()
    current = await WordPressService.get_category(**kw, category_id=category_id)
    existing = str(current.get('description') or '').strip()
    if existing and MARKER not in existing:
        return automation_store.guide_upsert(
            category_id, row['path'], state='skipped', error='This category already has a hand-written description; left unchanged.', content=''
        )
    await WordPressService.update_category(**kw, category_id=category_id, description=row['content'])
    link = next((c['link'] for c in await links.store_categories() if c.get('id') == category_id), row.get('link'))
    return automation_store.guide_upsert(
        category_id, row['path'], state='published', content='', error=None, link=link, published_at=automation_store.now_iso()
    )


# ---------------------------------------------------------------- the daily run
async def run_guide_job(run_id: int, cfg: GuideAutomationSettings, *, dry_run: bool) -> tuple[str, dict[str, Any]]:
    """Publish approved guides up to the daily limit. With auto_publish on, also draft new ones and publish them."""
    global _busy
    stats = {'published': 0, 'drafted': 0, 'skipped': 0, 'errors': 0}
    quota = cfg.guides_per_day
    if _busy:
        automation_store.add_run_item(run_id, status='skipped', message='Drafting is already running; try again later.')
        return 'completed', stats
    _busy = True
    try:
        approved = automation_store.guide_ids_in_state('approved', quota)
        for cid in approved:
            row = automation_store.guide_get(cid) or {}
            if dry_run:
                stats['published'] += 1
                automation_store.add_run_item(run_id, status='dry_run', message=f'Would publish guide: {row.get("path")}', category_id=cid, category_name=row.get('path'))
                continue
            try:
                out = await publish_guide(cid)
                ok = out['state'] == 'published'
                stats['published' if ok else 'skipped'] += 1
                automation_store.add_run_item(run_id, status='created' if ok else 'skipped', message=(f'Published guide: {out["path"]}' if ok else out.get('error') or 'Skipped'), category_id=cid, category_name=out['path'], ref_url=out.get('link'))
            except Exception as exc:  # noqa: BLE001
                stats['errors'] += 1
                automation_store.add_run_item(run_id, status='error', message=f'{exc.__class__.__name__}: {str(exc)[:200]}', category_id=cid, category_name=row.get('path'))
            automation_store.update_run_summary(run_id, dict(stats))
        left = quota - stats['published']
        if cfg.auto_publish and left > 0 and not dry_run:
            attempts = 0
            for leaf in await pending_categories(min(left * 4, 200)):
                if stats['published'] >= quota or attempts >= left * 4:
                    break
                attempts += 1
                row = await generate_guide(leaf['id'], cfg)
                if row['state'] != 'generated':
                    stats['skipped' if row['state'] == 'skipped' else 'errors'] += 1
                    automation_store.add_run_item(run_id, status='skipped' if row['state'] == 'skipped' else 'error', message=f'{leaf["path"]}: {row.get("error")}', category_id=leaf['id'], category_name=leaf['path'])
                    continue
                stats['drafted'] += 1
                try:
                    out = await publish_guide(leaf['id'])
                    ok = out['state'] == 'published'
                    stats['published' if ok else 'skipped'] += 1
                    automation_store.add_run_item(run_id, status='created' if ok else 'skipped', message=(f'Published guide: {leaf["path"]}' if ok else out.get('error') or 'Skipped'), category_id=leaf['id'], category_name=leaf['path'], ref_url=out.get('link'))
                except Exception as exc:  # noqa: BLE001
                    stats['errors'] += 1
                    automation_store.add_run_item(run_id, status='error', message=f'{exc.__class__.__name__}: {str(exc)[:200]}', category_id=leaf['id'], category_name=leaf['path'])
                automation_store.update_run_summary(run_id, dict(stats))
    finally:
        _busy = False
    status = 'failed' if stats['errors'] and not stats['published'] else 'partial' if stats['errors'] else 'completed'
    return status, stats

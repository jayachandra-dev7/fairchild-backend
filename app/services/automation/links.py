"""Keep blog links real.

Claude tends to invent URLs when asked for "internal links", and product permalinks can stop loading (draft or deleted
products). Everything here fails open: if the store or a site cannot be reached, the text is returned unchanged.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.services.platform_auth_service import platform_auth_service

_ANCHOR = re.compile(r'<a\b([^>]*?)\bhref\s*=\s*(["\'])(.*?)\2([^>]*)>(.*?)</a>', re.I | re.S)
_TAG = re.compile(r'<[^>]+>')
_WORD = re.compile(r'[a-z0-9]{3,}')
_STOP = {'the', 'and', 'for', 'with', 'best', 'top', 'how', 'our', 'your', 'shop', 'more', 'all', 'now', 'here', 'click', 'see', 'read', 'link'}
_SKIP_SCHEMES = ('mailto:', 'tel:', '#', 'javascript:')
_CAT_TTL = 3600
_cat_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _base(domain: str) -> str:
    base = str(domain).strip().rstrip('/')
    return base if base.startswith('http') else f'https://{base}'


def _creds() -> dict[str, Any] | None:
    return platform_auth_service.get_credentials('wordpress')


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


async def store_categories(creds: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Real product-category pages that have products: [{name, link, count}]. Cached for an hour; [] when unavailable."""
    creds = creds or _creds()
    if not creds:
        return []
    base = _base(creds['domain'])
    hit = _cat_cache.get(base)
    if hit and time.time() - hit[0] < _CAT_TTL:
        return hit[1]
    auth = (creds['wc_consumer_key'], creds['wc_consumer_secret'])
    cats: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for page in range(1, 6):
                resp = await client.get(f'{base}/wp-json/wp/v2/product_cat', params={'per_page': 100, 'page': page, 'hide_empty': 'true', '_fields': 'id,name,link,count'}, auth=auth)
                if resp.status_code >= 400:
                    break
                rows = resp.json()
                cats += [{'name': re.sub('&amp;', '&', str(r.get('name') or '')), 'link': str(r['link']), 'count': int(r.get('count') or 0)} for r in rows if r.get('link')]
                if len(rows) < 100:
                    break
            if not cats:  # product_cat not exposed over REST: build the standard WooCommerce URL from the slug
                for page in range(1, 6):
                    resp = await client.get(f'{base}/wp-json/wc/v3/products/categories', params={'per_page': 100, 'page': page, 'hide_empty': 'true'}, auth=auth)
                    if resp.status_code >= 400:
                        break
                    rows = resp.json()
                    cats += [{'name': str(r.get('name') or ''), 'link': f'{base}/product-category/{r["slug"]}/', 'count': int(r.get('count') or 0)} for r in rows if r.get('slug')]
                    if len(rows) < 100:
                        break
    except (httpx.HTTPError, ValueError, KeyError):
        return []
    cats = [c for c in cats if c['count'] > 0 and c['name'].lower() != 'uncategorized']
    if cats:
        _cat_cache[base] = (time.time(), cats)
    return cats


def closest_category(text: str, cats: list[dict[str, Any]]) -> dict[str, Any] | None:
    want = _tokens(text)
    best, best_score = None, 0
    for c in cats:
        score = len(want & _tokens(c['name']))
        if score > best_score or (score == best_score and score and best and c['count'] > best['count']):
            best, best_score = c, score
    return best if best_score else None


async def category_hint(text: str, limit: int = 20) -> str:
    """A prompt fragment listing real category pages Claude may link to (most relevant first). '' when none are known."""
    cats = await store_categories()
    if not cats:
        return ''
    want = _tokens(text)
    ranked = sorted(cats, key=lambda c: (-len(want & _tokens(c['name'])), -c['count']))[:limit]
    lines = '\n'.join(f'- {c["name"]}: {c["link"]}' for c in ranked)
    return (
        'Real store category pages you may link to (use ONLY these URLs, or the product URLs given; never invent or guess any other URL):\n'
        f'{lines}\n'
    )


async def _is_dead(client: httpx.AsyncClient, url: str) -> bool:
    """Dead = 404/410 or the host cannot be reached. Anything else (403, 429, timeouts) counts as alive."""
    try:
        resp = await client.head(url, follow_redirects=True)
        if resp.status_code in (405, 501):
            resp = await client.get(url, follow_redirects=True)
        return resp.status_code in (404, 410)
    except (httpx.ConnectError, httpx.InvalidURL, httpx.UnsupportedProtocol):
        return True
    except httpx.HTTPError:
        return False


async def dead_urls(urls: list[str]) -> set[str]:
    """Which of these URLs are dead (checked concurrently, 10 at a time)."""
    uniq = list(dict.fromkeys(u for u in urls if u))
    if not uniq:
        return set()
    sem = asyncio.Semaphore(10)
    async with httpx.AsyncClient(timeout=8, headers={'User-Agent': 'Mozilla/5.0 (link-check)'}) as client:

        async def one(u: str) -> tuple[str, bool]:
            async with sem:
                return u, await _is_dead(client, u)

        return {u for u, dead in await asyncio.gather(*(one(u) for u in uniq)) if dead}


async def sanitize_links(html: str, *, creds: dict[str, Any] | None = None, allowed: set[str] | frozenset[str] = frozenset()) -> tuple[str, list[dict[str, str]]]:
    """Replace every dead link with the closest real category page (or plain text). Returns (html, changes)."""
    creds = creds or _creds()
    base = _base(creds['domain']) if creds else ''
    matches = _ANCHOR.findall(html)
    hrefs = {m[2].strip() for m in matches}
    to_check: dict[str, str] = {}
    for h in hrefs:
        if not h or h.lower().startswith(_SKIP_SCHEMES) or h in allowed:
            continue
        full = urljoin(base + '/', h) if base else h
        if urlparse(full).scheme in ('http', 'https'):
            to_check[h] = full
    dead_full = await dead_urls(list(to_check.values()))
    dead = {h for h, full in to_check.items() if full in dead_full}
    if not dead:
        return html, []
    cats = await store_categories(creds)
    changes: list[dict[str, str]] = []

    def fix(m: re.Match[str]) -> str:
        pre, quote, href, post, inner = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        if href.strip() not in dead:
            return m.group(0)
        cat = closest_category(_TAG.sub(' ', inner), cats)
        changes.append({'href': href.strip(), 'text': _TAG.sub('', inner).strip()[:80], 'replacement': cat['link'] if cat else ''})
        if cat:
            return f'<a{pre}href={quote}{cat["link"]}{quote}{post}>{inner}</a>'
        return inner

    return _ANCHOR.sub(fix, html), changes


async def clean_html(html: str, allowed: set[str] | frozenset[str] = frozenset()) -> str:
    """Fail-open wrapper used by the blog jobs: never raises, returns the input when anything goes wrong."""
    try:
        cleaned, _ = await sanitize_links(html, allowed=allowed)
        return cleaned
    except Exception:  # noqa: BLE001
        return html

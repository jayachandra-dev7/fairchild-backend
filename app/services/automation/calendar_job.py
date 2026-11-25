"""Content calendar: read an uploaded plan, draft each article, schedule approved ones in WordPress.

Flow: upload (parse, nothing saved) -> confirm (rows saved as 'planned') -> generate (Claude drafts, 'generated') ->
approve in the UI ('approved') -> schedule (WordPress 'future' post, state 'scheduled'). WordPress publishes the
post itself on its date, so nothing here has to be running at that moment.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.db import automation_store
from app.schemas.wordpress.post import WordPressPostCreateRequest
from app.services.automation import blog_job, claude_review
from app.services.platform_auth_service import platform_auth_service
from app.services.wordpress.service import WordPressService
from app.utils.retry import run_with_retry

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
DISCLOSURE = (
    '<p><em>Disclosure: some links in this article are affiliate links. If you buy through them we may earn a small '
    'commission at no extra cost to you.</em></p>'
)
# Local-experience / city articles are the ones most likely to contain wrong venues, hours or prices.
_FACT_CHECK = re.compile(r'\b(new york|nyc|orlando|tampa|chicago|florida|things to do|activities|getaway|getaways|malls?)\b', re.I)
_STOP = {'best', 'with', 'that', 'your', 'from', 'what', 'this', 'under', 'worth', 'buying', 'every', 'should', 'make', 'look', 'more', 'than', 'they', 'are', 'for', 'the', 'and', 'how', 'you', 'ideas', 'guide'}

PARSE_SYSTEM = (
    'You read content-marketing plans and extract the planned articles exactly as written. Never invent articles. '
    'Respond with a single JSON object and nothing else.'
)
ARTICLE_SYSTEM = (
    'You are a skilled SEO editor for a US shopping, deals and lifestyle website. Write original, genuinely useful articles. '
    'Never invent prices, ratings, specs, venue names, opening hours or event details. Respond with a single JSON object and nothing else.'
)

_running = False  # one background generation at a time (the SQLite store is shared)


def needs_fact_check(row: dict[str, Any]) -> bool:
    return bool(_FACT_CHECK.search(f"{row.get('title', '')} {row.get('keyword', '')}"))


# ---------------------------------------------------------------- reading the uploaded plan
def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read('word/document.xml').decode('utf-8', 'ignore')
    xml = re.sub(r'</w:p>', '\n', xml)
    xml = re.sub(r'</w:tc>', '\t', xml)
    return re.sub(r'<[^>]+>', '', xml)


def spread_dates(rows: list[dict[str, Any]], *, start: date | None, per_week: int) -> None:
    """Fill missing publish dates in place: weekdays only, at most `per_week` posts per week, starting after the last dated row."""
    dated = [date.fromisoformat(r['publish_date']) for r in rows if r.get('publish_date')]
    cursor = start or ((max(dated) + timedelta(days=1)) if dated else date.today() + timedelta(days=1))
    per_week = max(1, min(per_week, 5))
    week_key, in_week = None, 0
    for r in rows:
        if r.get('publish_date'):
            continue
        while True:
            if cursor.weekday() >= 5:
                cursor += timedelta(days=1)
                continue
            key = cursor.isocalendar()[:2]
            if key != week_key:
                week_key, in_week = key, 0
            if in_week >= per_week:
                cursor += timedelta(days=1)
                continue
            break
        r['publish_date'] = cursor.isoformat()
        in_week += 1
        cursor += timedelta(days=1)


async def parse_plan(*, filename: str, data: bytes | None, text: str | None, start: date | None, per_week: int, instructions: str = '') -> dict[str, Any]:
    """Ask Claude to pull the planned articles out of an uploaded plan. Nothing is saved."""
    name = (filename or '').lower()
    pdf_b64: str | None = None
    body = ''
    if data is not None:
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError('File is too large (8 MB max).')
        if name.endswith('.pdf') or data[:4] == b'%PDF':
            pdf_b64 = base64.b64encode(data).decode()
        elif name.endswith('.docx'):
            body = _docx_text(data)
        else:
            body = data.decode('utf-8', 'ignore')
    else:
        body = text or ''
    if not pdf_b64 and not body.strip():
        raise ValueError('The plan is empty. Upload a PDF, DOCX, CSV or text file, or paste the plan.')

    prompt = (
        f"Today is {date.today().isoformat()}. The attached/pasted document is a content plan for a website's blog. "
        'List EVERY planned article once (use the article backlog; do not duplicate rows from calendar tables). '
        'For each article return its title, its target keyword (empty string if none), and its publish date as YYYY-MM-DD '
        'if the plan gives one in any calendar/schedule (infer the year from context: dates roll into the next year after December; '
        'match calendar rows to articles by number or title). Use null for the date when the plan gives none. '
        'Also return "guidelines": a short plain-text summary (max 600 characters) of any production rules in the plan '
        '(word count, keyword usage, links, disclosure, tone). Return exactly:\n'
        '{"guidelines":"...","articles":[{"title":"...","keyword":"...","publish_date":"2026-11-26"}]}'
    )
    if instructions.strip():
        prompt += (
            '\n\nEDITOR INSTRUCTIONS (follow these for dates, order and which articles to include; they override the plan\'s own dates '
            f'and the rules above where they conflict):\n\"\"\"\n{instructions.strip()[:1500]}\n\"\"\"'
        )
    if body:
        prompt += f'\n\nPLAN:\n"""\n{body[:60000]}\n"""'
    raw = await claude_review.ask_claude(prompt, max_tokens=8000, temperature=0.0, system=PARSE_SYSTEM, pdf_base64=pdf_b64)
    data_json = claude_review.extract_json(raw)

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in data_json.get('articles') or []:
        title = str(a.get('title') or '').strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        d = str(a.get('publish_date') or '').strip()
        try:
            d = date.fromisoformat(d).isoformat() if d else ''
        except ValueError:
            d = ''
        rows.append({'title': title[:200], 'keyword': str(a.get('keyword') or '').strip()[:120], 'publish_date': d})
    if not rows:
        raise ValueError('No articles were found in that file. Check that it lists article titles.')
    spread_dates(rows, start=start, per_week=per_week)
    # Dated rows first, in date order; spread rows already follow the last dated row.
    rows.sort(key=lambda r: r['publish_date'])
    for r in rows:
        r['needs_fact_check'] = needs_fact_check(r)
    return {'articles': rows, 'guidelines': str(data_json.get('guidelines') or '').strip()[:1000]}


# ---------------------------------------------------------------- drafting
def _wp_kw() -> dict[str, Any]:
    creds = platform_auth_service.get_credentials('wordpress')
    if not creds:
        raise RuntimeError('WordPress credentials are not configured.')
    return {'domain': creds['domain'], 'wc_consumer_key': creds['wc_consumer_key'], 'wc_consumer_secret': creds['wc_consumer_secret']}


async def find_live_products(keyword: str, title: str, limit: int = 6) -> list[dict[str, Any]]:
    """Published WooCommerce products related to the article, for the 'live offers' section. Never raises."""
    try:
        kw = _wp_kw()
    except RuntimeError:
        return []
    base = str(kw['domain']).strip().rstrip('/')
    if not base.startswith('http'):
        base = f'https://{base}'
    words = [w for w in re.findall(r'[a-zA-Z]{4,}', f'{keyword} {title}'.lower()) if w not in _STOP]
    queries = [q for q in [keyword.strip(), *dict.fromkeys(words)][:4] if q]
    found: dict[int, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for q in queries:
                resp = await client.get(
                    f'{base}/wp-json/wc/v3/products',
                    params={'search': q, 'status': 'publish', 'per_page': 8, '_fields': 'id,name,permalink,price,regular_price,sale_price'},
                    auth=(kw['wc_consumer_key'], kw['wc_consumer_secret']),
                )
                if resp.status_code >= 400:
                    break
                for p in resp.json():
                    found.setdefault(int(p['id']), p)
                if len(found) >= limit:
                    break
    except (httpx.HTTPError, ValueError, KeyError):
        return []
    return list(found.values())[:limit]


def build_article_prompt(row: dict[str, Any], guidelines: str, products: list[dict[str, Any]]) -> str:
    items = [
        {'name': p.get('name'), 'price': p.get('price'), 'regular_price': p.get('regular_price') or None, 'url': p.get('permalink')}
        for p in products
    ]
    offers = (
        f'Live products from our store (feature 3-6 that genuinely fit, each as <a href="URL">name</a> in a short "Deals to check out" section; '
        f'use only these names, prices and URLs):\n{items}\n'
        if items
        else 'No matching store products: do not invent product links; link readers to the store\'s relevant category pages with a short call to action.\n'
    )
    local = (
        'This is a local/experience topic: do NOT name specific venues, restaurants, prices, opening hours or events. Give general, '
        'practical advice and tell readers to confirm details with official sources.\n'
        if needs_fact_check(row)
        else ''
    )
    return (
        f'Article title: "{row["title"]}"\nPrimary keyword: "{row["keyword"] or row["title"]}"\n'
        f'{("Plan guidelines: " + guidelines) if guidelines else ""}\n{offers}{local}\n'
        'Write 800-1,200 words as clean HTML (<h2>, <h3>, <p>, <ul>, <a>; no <html>/<body>, no <h1>, no scripts). '
        'Use the primary keyword naturally (intro and one heading) plus 3-5 related terms, descriptive headings, original practical advice, '
        'and a closing call to action. Add a short "You may also like" line pointing to relevant store category pages if no products are given. '
        'The excerpt must be a plain-text meta description of 140-155 characters. Return exactly:\n'
        '{"excerpt":"...","content":"<p>...</p>"}'
    )


async def generate_one(post_id: int) -> dict[str, Any] | None:
    row = automation_store.calendar_get(post_id)
    if not row:
        return None
    guidelines = automation_store.get_state(f'calendar_guidelines:{row["batch"]}', '') or ''
    try:
        products = await find_live_products(row['keyword'], row['title'])
        raw = await claude_review.ask_claude(
            build_article_prompt(row, guidelines, products), max_tokens=6000, temperature=0.7, system=ARTICLE_SYSTEM
        )
        data = claude_review.extract_json(raw)
        content = str(data.get('content') or '').strip()
        if not content:
            raise ValueError('Claude returned an empty article.')
        if 'affiliate' not in content.lower():
            content += DISCLOSURE
        return automation_store.calendar_update(
            post_id, state='generated', excerpt=str(data.get('excerpt') or '').strip()[:300], content=content, error=None
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning('calendar draft failed for %s: %s', post_id, exc)
        detail = f'{exc.response.status_code} {exc.response.text[:200]}' if isinstance(exc, httpx.HTTPStatusError) else f'{exc.__class__.__name__}: {str(exc)[:200]}'
        return automation_store.calendar_update(post_id, state='error', error=detail)


async def _generate_all(ids: list[int]) -> None:
    global _running
    try:
        for pid in ids:
            await generate_one(pid)
    finally:
        _running = False


def start_generation(ids: list[int]) -> int:
    """Marks the rows 'generating' and drafts them one after another in the background. Returns how many were queued."""
    global _running
    if _running:
        raise RuntimeError('Drafting is already running. Wait for it to finish.')
    queue = []
    for pid in ids:
        row = automation_store.calendar_get(pid)
        if row and row['state'] in ('planned', 'generated', 'approved', 'error'):
            automation_store.calendar_update(pid, state='generating', error=None)
            queue.append(pid)
    if queue:
        _running = True
        asyncio.get_running_loop().create_task(_generate_all(queue))
    return len(queue)


def is_generating() -> bool:
    return _running


# ---------------------------------------------------------------- scheduling
def publish_moment(day: str, hhmm: str, tz_name: str, *, now: datetime | None = None) -> tuple[str, str | None]:
    """Returns (wordpress status, date_gmt). A moment that has already passed publishes immediately."""
    try:
        zone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f'Unknown timezone: {tz_name}') from exc
    hh, mm = (int(x) for x in hhmm.split(':'))
    local = datetime.combine(date.fromisoformat(day), datetime.min.time()).replace(hour=hh, minute=mm, tzinfo=zone)
    utc = local.astimezone(timezone.utc)
    now = now or datetime.now(timezone.utc)
    if utc <= now + timedelta(minutes=2):
        return 'publish', None
    return 'future', utc.strftime('%Y-%m-%dT%H:%M:%S')


async def schedule_one(post_id: int, hhmm: str, tz_name: str, *, now: bool = False) -> dict[str, Any]:
    """Send one article to WordPress. `now=True` publishes it immediately, whatever its date or time."""
    row = automation_store.calendar_get(post_id)
    if not row or row['state'] not in ('generated', 'approved', 'scheduled') or not row['content']:
        raise ValueError('Generate and review the article first.')
    if not now and row['state'] not in ('approved', 'scheduled'):
        raise ValueError('Only approved articles can be scheduled.')
    kw = _wp_kw()
    if now:
        wp_status, date_gmt = 'publish', datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    else:
        wp_status, date_gmt = publish_moment(row['publish_date'], row.get('publish_time') or hhmm, tz_name)
    cat_id = await blog_job.blog_category_id(kw)
    payload = WordPressPostCreateRequest(
        title=row['title'], content=row['content'], status=wp_status, excerpt=row['excerpt'],
        categories=[cat_id] if cat_id else [], date_gmt=date_gmt,
    )
    if row['wp_post_id']:  # already in WordPress: update it so a changed date or edit never creates a duplicate
        post = await run_with_retry(step='wordpress_post_update', operation=lambda: WordPressService.update_post(**kw, post_id=int(row['wp_post_id']), payload=payload))
    else:
        post = await run_with_retry(step='wordpress_post_create', operation=lambda: WordPressService.create_post(**kw, payload=payload))
        automation_store.record_blog(wp_post_id=post.get('id'), category_id=None, title=row['title'], link=post.get('link'))
    return automation_store.calendar_update(
        post_id, state='scheduled', wp_post_id=post.get('id'), link=post.get('link'), error=None,
        scheduled_for='now' if wp_status == 'publish' else date_gmt,
    ) or row

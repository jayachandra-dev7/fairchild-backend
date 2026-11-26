"""Daily blog job: random categories -> Claude writes a post featuring recent products -> WordPress post."""
from __future__ import annotations

import random
from typing import Any

import httpx

from app.db import automation_store
from app.schemas.automation import BlogAutomationSettings
from app.schemas.wordpress.post import WordPressPostCreateRequest
from app.services.automation import categories, claude_review, links
from app.services.platform_auth_service import platform_auth_service
from app.services.wordpress.service import WordPressService
from app.utils.retry import run_with_retry

BLOG_SYSTEM = (
    'You are a helpful shopping editor for a deals website. Write accurate, readable blog posts. Use ONLY the facts '
    'provided; never invent specs, ratings or claims, and do not promise prices will last. Never invent or guess URLs: only link '
    'to the product URLs you are given. Respond with a single JSON '
    'object and nothing else.'
)


BLOG_CATEGORY_NAME = 'Blog'
_blog_category_id: dict[str, int] = {}


async def blog_category_id(kw: dict[str, Any]) -> int | None:
    """Id of the WordPress post category "Blog" (created if missing). Posts sent without a category end up in none,
    so every automated post is filed here. Returns None when it cannot be resolved, so a post is never lost over it."""
    key = str(kw['domain'])
    if key in _blog_category_id:
        return _blog_category_id[key]
    base = str(kw['domain']).strip().rstrip('/')
    if not base.startswith('http'):
        base = f'https://{base}'
    auth = (kw['wc_consumer_key'], kw['wc_consumer_secret'])
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            found = await client.get(f'{base}/wp-json/wp/v2/categories', params={'slug': BLOG_CATEGORY_NAME.lower()}, auth=auth)
            found.raise_for_status()
            rows = found.json()
            if rows:
                cid = int(rows[0]['id'])
            else:
                made = await client.post(f'{base}/wp-json/wp/v2/categories', json={'name': BLOG_CATEGORY_NAME}, auth=auth)
                made.raise_for_status()
                cid = int(made.json()['id'])
    except (httpx.HTTPError, KeyError, ValueError):
        return None
    _blog_category_id[key] = cid
    return cid


def _discount(p: dict[str, Any]) -> float:
    try:
        price, sale = float(p['price']), float(p['sale_price'])
        return (price - sale) / price * 100 if price else 0.0
    except (TypeError, ValueError, KeyError):
        return 0.0


def pick_topics(cfg: BlogAutomationSettings, rng: random.Random | None = None) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Returns [(category, products)]; categories with fresh products first (random order), then generic guides."""
    rng = rng or random.Random()
    recent_blogged = automation_store.blog_categories_since(7)
    topics: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []

    with_products = [cid for cid in automation_store.categories_with_products(30) if cid not in recent_blogged]
    rng.shuffle(with_products)
    for cid in with_products:
        cat = categories.category_by_id(cid)
        if not cat:
            continue
        products = [p for p in automation_store.recent_products([cid], limit=6) if _discount(p) >= cfg.min_discount][:3]
        if products:
            topics.append((cat, products))
        if len(topics) >= cfg.blogs_per_day:
            return topics

    pool = [c for c in categories.all_categories() if c['depth'] <= 3 and c['id'] not in recent_blogged]
    rng.shuffle(pool)
    for cat in pool:
        if len(topics) >= cfg.blogs_per_day:
            break
        topics.append((cat, []))
    return topics


def build_prompt(cat: dict[str, Any], products: list[dict[str, Any]], hint: str) -> str:
    items = [
        {'name': p['name'], 'list_price': p['price'], 'sale_price': p['sale_price'], 'discount_percent': round(_discount(p)), 'url': p['permalink']}
        for p in products
    ]
    product_part = (
        f'Feature these products (each must appear with a link <a href="URL">name</a>):\n{items}\n'
        if items
        else 'No specific products: write a helpful buying guide for the category with practical tips, no product links.\n'
    )
    return (
        f'Category: "{cat["path"]}"\n{product_part}'
        f'{("Editor note: " + hint) if hint.strip() else ""}\n\n'
        'Write an engaging blog post of 450-650 words as clean HTML (<h2>, <p>, <ul>; no <html>/<body>, no <h1>). '
        'Include an intro, 2-3 sections, and a short closing. Return exactly:\n'
        '{"title":"SEO-friendly title under 65 characters","excerpt":"1-2 sentence summary, plain text","content":"<p>...</p>"}'
    )


async def _prompt_with_links(cat: dict[str, Any], products: list[dict[str, Any]], hint: str) -> str:
    return build_prompt(cat, products, hint) + '\n\n' + await links.category_hint(cat['path'], limit=10)


async def run_blog_job(run_id: int, cfg: BlogAutomationSettings, *, dry_run: bool) -> tuple[str, dict[str, Any]]:
    creds = platform_auth_service.get_credentials('wordpress')
    if not creds and not dry_run:
        raise RuntimeError('WordPress credentials are not configured.')
    topics = pick_topics(cfg)
    stats = {'planned': len(topics), 'published': 0, 'would_publish': 0, 'errors': 0}
    automation_store.update_run_summary(run_id, stats)
    if not topics:
        automation_store.add_run_item(run_id, status='skipped', message='No categories available for blogs. Sync categories first.')

    for cat, products in topics:
        try:
            dead = await links.dead_urls([p['permalink'] for p in products if p.get('permalink')])
            products = [p for p in products if p.get('permalink') and p['permalink'] not in dead]  # drafts / deleted products do not load
            data = claude_review.extract_json(
                await claude_review.ask_claude(await _prompt_with_links(cat, products, cfg.topic_hint), max_tokens=4000, temperature=0.6, system=BLOG_SYSTEM)
            )
            title, content = str(data.get('title', '')).strip(), str(data.get('content', '')).strip()
            if not title or not content:
                raise ValueError('Claude returned an empty title or body')
            for p in products:  # guarantee product links exist even if the model dropped one
                if p['permalink'] and p['permalink'] not in content:
                    content += f'<p><a href="{p["permalink"]}">{p["name"]}</a></p>'
            content = await links.clean_html(content, allowed={p['permalink'] for p in products})
            if dry_run:
                stats['would_publish'] += 1
                automation_store.add_run_item(run_id, status='dry_run', message=f'Would publish: {title}', category_id=cat['id'], category_name=cat['path'])
                continue
            media = next((p['media_id'] for p in products if p.get('media_id')), None)
            kw = {'domain': creds['domain'], 'wc_consumer_key': creds['wc_consumer_key'], 'wc_consumer_secret': creds['wc_consumer_secret']}  # type: ignore[index]
            cat_id = await blog_category_id(kw)
            payload = WordPressPostCreateRequest(
                title=title, content=content, status=cfg.publish_status, excerpt=str(data.get('excerpt', '')), featured_media=media,
                categories=[cat_id] if cat_id else [],
            )
            post = await run_with_retry(step='wordpress_post_create', operation=lambda: WordPressService.create_post(**kw, payload=payload))
            automation_store.record_blog(wp_post_id=post.get('id'), category_id=cat['id'], title=title, link=post.get('link'))
            stats['published'] += 1
            automation_store.add_run_item(run_id, status='created', message=f'Published: {title}', category_id=cat['id'], category_name=cat['path'], ref_id=post.get('id'), ref_url=post.get('link'))
        except Exception as exc:  # noqa: BLE001 - one failed post must not stop the rest
            stats['errors'] += 1
            detail = f'{exc.response.status_code} {exc.response.text[:260]}' if isinstance(exc, httpx.HTTPStatusError) else str(exc)[:200]
            automation_store.add_run_item(run_id, status='error', message=f'{exc.__class__.__name__}: {detail}', category_id=cat['id'], category_name=cat['path'])
        finally:
            automation_store.update_run_summary(run_id, dict(stats))

    done = stats['published'] + stats['would_publish']
    status = 'failed' if stats['errors'] and not done else 'partial' if stats['errors'] else 'completed'
    return status, stats


# ---------------------------------------------------------------- custom (one-off, human-reviewed) blog

CUSTOM_SYSTEM = (
    'You are a skilled blog writer for a shopping and deals website. Follow the editor\'s instructions closely. '
    'Never invent specs, ratings, prices or claims about specific products. Never invent or guess URLs: only use URLs the editor '
    'or the prompt gives you. Respond with a single JSON object and nothing else.'
)


async def generate_custom_blog(prompt: str) -> dict[str, str]:
    """Claude drafts a post from the editor's prompt. Nothing is published; the editor reviews it first."""
    text = (
        f'Editor instructions:\n"""\n{prompt.strip()}\n"""\n\n{await links.category_hint(prompt)}\n'
        'Write the blog post as clean HTML (<h2>, <p>, <ul>, <a>; no <html>/<body>, no <h1>, no scripts). '
        'If the instructions give a length, tone or links, follow them; otherwise write 450-650 words. Return exactly:\n'
        '{"title":"SEO-friendly title under 65 characters","excerpt":"1-2 sentence summary, plain text","content":"<p>...</p>"}'
    )
    data = claude_review.extract_json(await claude_review.ask_claude(text, max_tokens=4000, temperature=0.7, system=CUSTOM_SYSTEM))
    title, content = str(data.get('title', '')).strip(), str(data.get('content', '')).strip()
    if not title or not content:
        raise ValueError('Claude returned an empty title or body. Try rewording the prompt.')
    return {'title': title, 'excerpt': str(data.get('excerpt', '')).strip(), 'content': await links.clean_html(content)}


async def publish_custom_blog(*, title: str, content: str, excerpt: str, status: str) -> dict[str, Any]:
    creds = platform_auth_service.get_credentials('wordpress')
    if not creds:
        raise RuntimeError('WordPress credentials are not configured.')
    kw = {'domain': creds['domain'], 'wc_consumer_key': creds['wc_consumer_key'], 'wc_consumer_secret': creds['wc_consumer_secret']}
    cat_id = await blog_category_id(kw)
    content = await links.clean_html(content)
    payload = WordPressPostCreateRequest(title=title, content=content, status=status, excerpt=excerpt, categories=[cat_id] if cat_id else [])
    post = await run_with_retry(step='wordpress_post_create', operation=lambda: WordPressService.create_post(**kw, payload=payload))
    automation_store.record_blog(wp_post_id=post.get('id'), category_id=None, title=title, link=post.get('link'))
    return {'id': post.get('id'), 'link': post.get('link'), 'status': post.get('status')}

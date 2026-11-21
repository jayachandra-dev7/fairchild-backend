"""Daily product job: leaf category -> CJ+Impact deals -> filters -> Claude review -> WordPress product."""
from __future__ import annotations

import asyncio
import bisect
from collections import Counter
import logging
import mimetypes
import re
from typing import Any

import httpx

from app.db import automation_store
from app.schemas.automation import Candidate, ProductAutomationSettings
from app.schemas.wordpress.product import WooProductCategory, WooProductCreateRequest, WooProductImage, WooProductMetaData
from app.services.automation import categories, claude_review, dedupe, existing_products, filters, sourcing
from app.services.image.renderer import render_branded_image
from app.services.platform_auth_service import platform_auth_service
from app.services.wordpress.service import WordPressService
from app.utils.image_validation import fetch_and_validate_image_url
from app.utils.retry import run_with_retry

logger = logging.getLogger(__name__)
CONCURRENCY = 3


def _slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:60] or 'product'


def _cursor(leaves: list[dict[str, Any]]) -> int:
    """Where the rotation continues: the first category after the last one processed (by id, so it survives a
    category re-sync); falls back to the stored position."""
    if not leaves:
        return 0
    last_id = automation_store.get_state('product_last_category_id')
    if last_id is not None:
        return bisect.bisect_right([c['id'] for c in leaves], int(last_id)) % len(leaves)
    return int(automation_store.get_state('product_cursor', '0') or 0) % len(leaves)


def pick_categories(
    cfg: ProductAutomationSettings, *, max_categories: int | None, category_ids: list[int] | None
) -> tuple[list[dict[str, Any]], int, int]:
    """Returns (categories_for_today, cursor_start, total_leaves). Round-robin over all leaves."""
    leaves = sorted(categories.leaf_categories(set(cfg.exclude_category_ids)), key=lambda c: c['id'])
    if category_ids:
        wanted = set(category_ids)
        return [c for c in leaves if c['id'] in wanted], 0, len(leaves)
    budget = min(max_categories or cfg.categories_per_day, len(leaves))
    cursor = _cursor(leaves)
    picked = [leaves[(cursor + i) % len(leaves)] for i in range(budget)]
    return picked, cursor, len(leaves)


class _Run:
    def __init__(self, run_id: int, cfg: ProductAutomationSettings, dry_run: bool) -> None:
        self.run_id = run_id
        self.cfg = cfg
        self.dry_run = dry_run
        self.healed = False
        self.verified_categories: set[int] = set()
        self.claimed: set[str] = set()  # fingerprint keys of products picked in this run (workers run concurrently)
        self.heal_lock = asyncio.Lock()
        self.meta: dict[str, Any] = {}  # planned/target/etc., kept in every progress save
        self.stats = {'categories_done': 0, 'created': 0, 'skipped_categories': 0, 'errors': 0, 'would_create': 0}

    def save(self) -> None:
        automation_store.update_run_summary(self.run_id, {**self.meta, **self.stats})

    def log(self, status: str, message: str, leaf: dict[str, Any] | None = None, **kw: Any) -> None:
        automation_store.add_run_item(
            self.run_id,
            status=status,
            message=message,
            category_id=leaf['id'] if leaf else None,
            category_name=leaf['path'] if leaf else None,
            **kw,
        )


def describe_error(exc: Exception) -> str:
    """Readable one-liner; for HTTP errors include the upstream response body (e.g. Anthropic's message)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f'{exc.__class__.__name__}: {exc.response.status_code} {exc.response.text[:260]}'
    return f'{exc.__class__.__name__}: {str(exc)[:300]}'


def _is_stale_category_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (400, 404):
        body = exc.response.text.lower()
        return 'term' in body or 'categor' in body
    return False


async def _heal(run: _Run, leaf: dict[str, Any]) -> dict[str, Any] | None:
    """Re-sync the category snapshot from WordPress (once per run) and re-resolve this category."""
    async with run.heal_lock:
        if not run.healed:
            run.healed = True
            snap = await categories.sync_snapshot()
            run.log('info', f"Category snapshot re-synced from WordPress ({snap['total']} categories).")
    return categories.category_by_id(leaf['id']) or categories.find_by_path(leaf['path'])


async def _category_exists(creds: dict[str, Any], category_id: int) -> bool | None:
    """True/False if WordPress says the category exists; None when it cannot be checked (then just proceed)."""
    base = str(creds['domain']).strip().rstrip('/')
    if not base.startswith('http'):
        base = f'https://{base}'
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f'{base}/wp-json/wc/v3/products/categories/{category_id}',
                auth=(creds['wc_consumer_key'], creds['wc_consumer_secret']),
            )
    except httpx.HTTPError:
        return None
    if resp.status_code == 404:
        return False
    return True if resp.status_code == 200 else None


async def _prepare_image(cand: Candidate, copy: dict[str, Any], cfg: ProductAutomationSettings) -> tuple[bytes, str, str]:
    """Returns (bytes, filename, content_type)."""
    title = str(copy.get('title') or cand.title)
    if cfg.branded_image:
        text = str(copy.get('image_text') or title)[:40]
        jpeg = await render_branded_image(template=cfg.template_id, title=text, image_url=cand.image_url)
        return jpeg, f'{_slug(title)}.jpg', 'image/jpeg'
    data, ctype = await run_with_retry(step='fetch_product_image', operation=lambda: fetch_and_validate_image_url(cand.image_url))
    ext = mimetypes.guess_extension(ctype.split(';')[0].strip()) or '.jpg'
    return data, f'{_slug(title)}{ext}', ctype.split(';')[0].strip()


async def _create_in_wordpress(run: _Run, leaf: dict[str, Any], cand: Candidate, copy: dict[str, Any]) -> dict[str, Any]:
    creds = platform_auth_service.get_credentials('wordpress')
    if not creds:
        raise RuntimeError('WordPress credentials are not configured.')
    kw = {'domain': creds['domain'], 'wc_consumer_key': creds['wc_consumer_key'], 'wc_consumer_secret': creds['wc_consumer_secret']}

    image_bytes, filename, ctype = await _prepare_image(cand, copy, run.cfg)
    media = await run_with_retry(
        step='wordpress_media_upload',
        operation=lambda: WordPressService.upload_media_bytes(**kw, filename=filename, content_type=ctype, file_bytes=image_bytes),
    )
    media_id = int(media['id'])

    def payload(category_id: int) -> WooProductCreateRequest:
        return WooProductCreateRequest(
            name=str(copy.get('title') or cand.title)[:120],
            type='external',
            status=run.cfg.publish_status,
            featured=False,
            description=str(copy.get('description') or ''),
            short_description=str(copy.get('short_description') or ''),
            external_url=cand.buy_url,
            button_text='Buy Now',
            regular_price=f'{cand.price:.2f}',
            sale_price=f'{cand.sale_price:.2f}',
            categories=[WooProductCategory(id=category_id)],
            images=[WooProductImage(id=media_id)],
            meta_data=[
                WooProductMetaData(key='vendor', value=cand.advertiser or cand.brand),
                WooProductMetaData(key='automation_source', value=cand.source),
                WooProductMetaData(key='automation_source_id', value=cand.source_id),
            ],
        )

    category_id = leaf['id']
    # WordPress silently files a product under "Uncategorized" when the category id no longer exists, so check first
    # (cached per run). A stale snapshot is then re-synced and the category re-resolved by its path.
    if category_id not in run.verified_categories:
        if await _category_exists(creds, category_id) is False:
            fresh = await _heal(run, leaf)
            if not fresh or await _category_exists(creds, fresh['id']) is False:
                raise RuntimeError(f'Category {leaf["id"]} ({leaf["path"]}) no longer exists in WordPress.')
            category_id = fresh['id']
        run.verified_categories.add(category_id)
    try:
        product = await run_with_retry(step='wordpress_product_create', operation=lambda: WordPressService.create_product(**kw, payload=payload(category_id)))
    except Exception as exc:  # noqa: BLE001
        if not _is_stale_category_error(exc):
            raise
        fresh = await _heal(run, leaf)
        if not fresh:
            raise RuntimeError(f'Category {leaf["id"]} ({leaf["path"]}) no longer exists in WordPress.') from exc
        category_id = fresh['id']
        product = await run_with_retry(step='wordpress_product_create', operation=lambda: WordPressService.create_product(**kw, payload=payload(category_id)))
    return {'product': product, 'media_id': media_id, 'category_id': category_id}


# A category is searched page after page until its results run out (an empty page, or a page that only repeats
# earlier results). These two are only runaway guards, far above anything a real category reaches.
SAFETY_MAX_PAGES = 500
SAFETY_MAX_REVIEW_ROUNDS = 500
MAX_REVIEWED_PER_KEYWORD = 100  # for each search keyword, Claude looks at up to this many products before the next keyword
EXTRA_CATEGORY_FACTOR = 10  # when categories fall short, at most planned*10+20 categories are tried in a run


def extension_leaves(cfg: ProductAutomationSettings, cursor: int, planned: int) -> list[dict[str, Any]]:
    """Leaves that come after today's planned categories in rotation order (used when categories fall short)."""
    leaves = sorted(categories.leaf_categories(set(cfg.exclude_category_ids)), key=lambda c: c['id'])
    if not leaves:
        return []
    limit = min(len(leaves), planned * EXTRA_CATEGORY_FACTOR + 20)
    return [leaves[(cursor + i) % len(leaves)] for i in range(planned, limit)]


async def _process_category(run: _Run, leaf: dict[str, Any], quota: int) -> int:
    """Create up to `quota` products for this category and return how many were made.

    Searches CJ + Impact (several Claude-suggested keywords, page by page), filters, picks at random, and only then
    lets Claude review + write copy. A rejected product is replaced by the next pick. Each keyword gets up to
    MAX_REVIEWED_PER_KEYWORD reviewed products; when every keyword is used up the category is skipped, so one hard
    category cannot stall the whole run.
    """
    cfg = run.cfg
    made = 0
    reviewed = 0
    claude_rejects = 0
    fetched = 0
    pages = 0
    why: Counter = Counter()
    tried: set[tuple[str, str]] = set()
    run.log('info', f'Searching for {quota} product(s)…', leaf)
    try:
        keywords = await claude_review.suggest_keywords(leaf) if cfg.claude_review else [leaf['name']]
        rounds = 0
        for kw in keywords:
            if made >= quota:
                break
            if len(keywords) > 1:
                run.log('info', f'Search keyword: "{kw}"', leaf)
            state: dict[str, Any] = {}
            page_no = 0
            fetched_keys: set[tuple[str, str]] = set()
            pool: list[Any] = []
            exhausted = False
            kw_reviewed = 0
            batch_size = quota - made + 2
            while made < quota and kw_reviewed < MAX_REVIEWED_PER_KEYWORD and rounds < SAFETY_MAX_REVIEW_ROUNDS:
                if not pool:
                    if exhausted or page_no >= SAFETY_MAX_PAGES:
                        break
                    candidates, errors = await sourcing.fetch_candidates(leaf, cfg, page_no, state=state, keyword=kw)
                    page_no += 1
                    pages += 1
                    for err in errors:
                        run.log('warn', f'Source error - {err}', leaf)
                    keys = {(c.source, c.source_id) for c in candidates}
                    if not keys or keys <= fetched_keys:  # nothing left, or a source repeats itself
                        exhausted = True
                        continue
                    fetched_keys |= keys
                    fetched += len(candidates)
                    kept, rejected = filters.filter_and_rank(candidates, cfg)
                    why.update(rejected)
                    pool = [c for c in kept if (c.source, c.source_id) not in tried]
                    continue

                rounds += 1
                need = quota - made
                size = min(max(need + 2, batch_size), MAX_REVIEWED_PER_KEYWORD - kw_reviewed)
                batch, pool = pool[:size], pool[size:]
                tried.update((c.source, c.source_id) for c in batch)
                reviewed += len(batch)
                kw_reviewed += len(batch)
                if cfg.claude_review:
                    approved, rejected_by_claude = await claude_review.review_candidates(leaf, batch, need, cfg.branded_image)
                    claude_rejects += len(rejected_by_claude)
                    for r in rejected_by_claude[:3]:
                        run.log('info', f"Claude rejected {r.get('id')}: {r.get('reason', '')}", leaf)
                else:
                    approved = [(c, claude_review.fallback_copy(c)) for c in batch[:need]]

                if not approved:
                    batch_size = 10  # nothing fit: review bigger batches so a bad search burns fewer Claude calls

                for cand, copy in approved:
                    if made >= quota:
                        break
                    label = f"{copy.get('title') or cand.title} ({cand.source}, ${cand.sale_price:.2f}, -{cand.discount:.0f}%)"
                    fp = dedupe.fingerprint(cand)
                    if run.claimed.intersection(fp.keys()):  # another worker/category already picked this product
                        why['already picked in this run'] += 1
                        continue
                    run.claimed.update(fp.keys())  # no await between the check and the claim, so this cannot race
                    if run.dry_run:
                        made += 1
                        run.stats['would_create'] += 1
                        run.log('dry_run', f'Would create: {label}', leaf)
                        run.save()
                        continue
                    try:
                        res = await _create_in_wordpress(run, leaf, cand, copy)
                    except Exception as exc:  # noqa: BLE001 - one bad product must not stop the category
                        run.stats['errors'] += 1
                        run.claimed.difference_update(fp.keys())
                        run.log('error', f'{label}: {describe_error(exc)}', leaf)
                        continue
                    product = res['product']
                    automation_store.record_product(
                        source=cand.source, source_id=cand.source_id, wp_product_id=product.get('id'), media_id=res['media_id'],
                        category_id=res['category_id'], name=str(copy.get('title') or cand.title), permalink=product.get('permalink'),
                        image_url=None, price=f'{cand.price:.2f}', sale_price=f'{cand.sale_price:.2f}',
                        core_id=fp.core_id, title_key=fp.title_key, image_key=fp.image_key, desc_key=fp.desc_key,
                    )
                    made += 1
                    run.stats['created'] += 1
                    run.log('created', f'Created: {label}', leaf, ref_id=product.get('id'), ref_url=product.get('permalink'))
                    run.save()

        gave_up = made < quota
        if made == 0:
            run.stats['skipped_categories'] += 1
            reasons = ', '.join(f'{k} x{v}' for k, v in why.most_common(3))
            detail = f'{fetched} fetched over {pages} page(s), {reviewed} reviewed' + (f'; {reasons}' if reasons else '') + (f'; Claude rejected {claude_rejects}' if claude_rejects else '')
            run.log('skipped', f'No usable products ({detail}){" - tried every keyword" if gave_up else ""}.', leaf)
        elif made < quota:
            run.log('warn', f'Only {made} of {quota} found ({reviewed} products reviewed across {len(keywords)} keyword(s)) - moving on.', leaf)
    except Exception as exc:  # noqa: BLE001 - isolate failures per category
        run.stats['errors'] += 1
        logger.exception('automation_category_failed')
        run.log('error', describe_error(exc), leaf)
    finally:
        run.stats['categories_done'] += 1
        run.save()
    return made


def _save_cursor(cursor: int, state: dict[str, Any], total: int) -> int:
    """Remember how far the rotation got so the next run continues with the following categories."""
    new_cursor = (cursor + state['picked']) % total
    automation_store.set_state('product_cursor', str(new_cursor))
    if state.get('last'):
        automation_store.set_state('product_last_category_id', str(state['last']['id']))
    return new_cursor


async def run_product_job(
    run_id: int,
    cfg: ProductAutomationSettings,
    *,
    dry_run: bool,
    max_categories: int | None = None,
    category_ids: list[int] | None = None,
) -> tuple[str, dict[str, Any]]:
    run = _Run(run_id, cfg, dry_run)
    todays, cursor, total = pick_categories(cfg, max_categories=max_categories, category_ids=category_ids)
    per_cat = cfg.products_per_category
    target = len(todays) * per_cat
    summary: dict[str, Any] = {'categories_planned': len(todays), 'target': target, 'total_leaf_categories': total, 'cursor_start': cursor, **run.stats}
    run.meta = {k: v for k, v in summary.items() if k not in run.stats}
    run.save()
    await existing_products.seed(run)
    run.log('info', f'Goal: {target} products ({len(todays)} categories x {per_cat}). Starting at category position {cursor} of {total}.')

    queue: list[dict[str, Any]] = list(todays)
    extras: list[dict[str, Any]] | None = None  # loaded lazily, only if categories fall short
    state: dict[str, Any] = {'reserved': 0, 'inflight': 0, 'picked': 0, 'last': None}
    cond = asyncio.Condition()
    announced = False

    def made_total() -> int:
        return run.stats['created'] + run.stats['would_create']

    async def next_leaf() -> tuple[dict[str, Any], int] | None:
        nonlocal extras, announced
        async with cond:
            while True:
                need = target - made_total() - state['reserved']
                if need > 0:
                    if not queue and extras is None and not category_ids:
                        extras = extension_leaves(cfg, cursor, len(todays))
                        queue.extend(extras)
                    if queue:
                        leaf = queue.pop(0)
                        if state['picked'] >= len(todays) and not announced:
                            announced = True
                            run.log('info', f'Some categories had too few usable products - continuing with the next categories to reach {target}.')
                        quota = min(per_cat, need)
                        state['reserved'] += quota
                        state['inflight'] += 1
                        state['picked'] += 1
                        state['last'] = leaf
                        return leaf, quota
                if state['inflight'] == 0:
                    return None
                await cond.wait()

    async def worker() -> None:
        while True:
            picked = await next_leaf()
            if picked is None:
                return
            leaf, quota = picked
            try:
                await _process_category(run, leaf, quota)
            finally:
                async with cond:
                    state['reserved'] -= quota
                    state['inflight'] -= 1
                    cond.notify_all()

    try:
        await asyncio.gather(*(worker() for _ in range(CONCURRENCY)))
    except asyncio.CancelledError:
        if not dry_run and not category_ids and total:  # keep the rotation moving past what was already processed
            _save_cursor(cursor, state, total)
        raise

    if not dry_run and not category_ids and total:
        new_cursor = _save_cursor(cursor, state, total)
        summary['cursor_end'] = new_cursor
        summary['cycle_progress_pct'] = round(new_cursor / total * 100, 1)
    summary.update(run.stats)
    summary['shortfall'] = max(0, target - made_total())
    if summary['shortfall']:
        run.log('warn', f'Finished with {made_total()} of {target} products - ran out of usable deals in the categories tried.')
    if run.stats['errors'] and not made_total():
        status = 'failed'
    elif run.stats['errors']:
        status = 'partial'
    else:
        status = 'completed'
    return status, summary

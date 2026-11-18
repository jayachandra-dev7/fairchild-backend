"""Fetch discounted products for a category from CJ and Impact and normalise them."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from app.core.config import get_settings
from app.schemas.automation import Candidate, ProductAutomationSettings
from app.schemas.cj.ads_query import CJAdsProductsQueryRequest
from app.services.automation import sources
from app.services.cj.ads_query_service import CJAdsQueryService
from app.services.impact.campaign_service import ImpactCampaignService
from app.services.platform_auth_service import platform_auth_service
from app.utils.retry import run_with_retry

logger = logging.getLogger(__name__)
FETCH_LIMIT = 40


def _num(value: Any) -> float:
    try:
        return float(str(value).replace(',', '').replace('$', '').strip() or 0)
    except ValueError:
        return 0.0


def _clean_keyword(text: str) -> str:
    # CJ's GraphQL query is built by string interpolation: strip quotes/backslashes so a category name
    # such as `Men's "Classic" Watches` cannot break the query.
    return re.sub(r'\s+', ' ', re.sub(r'["\\\\]', ' ', text)).strip()


def _relevance(leaf: dict[str, Any], source_category: str, title: str) -> int:
    name = leaf['name'].lower()
    score = 0
    if source_category and name in source_category.lower():
        score += 2
    if source_category and leaf['path'].lower() in source_category.lower():
        score += 2
    if name in title.lower():
        score += 1
    return score


def normalize_cj(item: dict[str, Any], leaf: dict[str, Any]) -> Candidate | None:
    price = _num((item.get('price') or {}).get('amount'))
    sale = _num((item.get('salePrice') or {}).get('amount'))
    if not price or not sale or sale >= price:
        return None
    link_code = item.get('linkCode') or {}
    avail = str(item.get('availability') or 'IN_STOCK').upper().replace(' ', '').replace('_', '')
    title = str(item.get('title') or '').strip()
    return Candidate(
        source='cj',
        source_id=f"{item.get('advertiserId', '')}:{item.get('id', '')}",
        title=title,
        description=str(item.get('description') or ''),
        advertiser=str(item.get('advertiserName') or ''),
        image_url=str(item.get('imageLink') or link_code.get('imageUrl') or ''),
        buy_url=str(link_code.get('clickUrl') or item.get('link') or ''),
        price=price,
        sale_price=sale,
        discount=round((price - sale) / price * 100, 1),
        currency=str((item.get('price') or {}).get('currency') or 'USD'),
        in_stock=avail in ('INSTOCK', ''),
        relevance=_relevance(leaf, '', title),
    )


def normalize_impact(item: dict[str, Any], leaf: dict[str, Any]) -> Candidate | None:
    price = _num(item.get('OriginalPrice'))
    sale = _num(item.get('CurrentPrice'))
    if not price or not sale or sale >= price:
        return None
    stock = str(item.get('StockAvailability') or '').replace(' ', '').lower()
    category = str(item.get('Category') or '')
    title = str(item.get('Name') or '').strip()
    return Candidate(
        source='impact',
        source_id=str(item.get('Id') or f"{item.get('CampaignId')}_{item.get('CatalogItemId')}"),
        title=title,
        description=str(item.get('Description') or ''),
        brand=str(item.get('Manufacturer') or ''),
        advertiser=str(item.get('CampaignName') or ''),
        image_url=str(item.get('ImageUrl') or ''),
        buy_url=str(item.get('Url') or ''),
        price=price,
        sale_price=sale,
        discount=round(_num(item.get('DiscountPercentage')) or (price - sale) / price * 100, 1),
        currency=str(item.get('Currency') or 'USD'),
        source_category=category,
        in_stock=stock in ('instock', ''),
        adult=str(item.get('Adult') or '').strip().lower() in ('true', 'yes', '1', 'y'),
        relevance=_relevance(leaf, category, title),
    )


async def _fetch_cj(leaf: dict[str, Any], cfg: ProductAutomationSettings, keyword: str, cursor: str | None) -> tuple[list[Candidate], str | None]:
    """One CJ page. CJ paginates with an opaque `nextPage` cursor (offset only works for the first page).
    Returns (candidates, next_cursor); next_cursor None = no more pages."""
    token = platform_auth_service.get_token('cj')
    if not token:
        raise RuntimeError('CJ token is not configured.')
    s = get_settings()
    request = CJAdsProductsQueryRequest(
        company_id=s.CJ_COMPANY_ID,
        pid=s.CJ_PID,
        keywords=[_clean_keyword(keyword)],
        advertiser_countries=['US'],
        partner_ids=cfg.cj_advertiser_ids,
        discount_percentage=cfg.min_discount,
        low_price=cfg.min_price,
        high_price=cfg.max_price,
        limit=FETCH_LIMIT,
        page=cursor,
    )
    payload = await run_with_retry(
        step='cj_products', operation=lambda: CJAdsQueryService.query_products(bearer_token=token, request=request)
    )
    if payload.get('errors'):
        raise RuntimeError(f"CJ GraphQL error: {str(payload['errors'])[:300]}")
    products = (payload.get('data') or {}).get('products') or {}
    rows = products.get('resultList') or []
    return [c for r in rows if (c := normalize_cj(r, leaf))], (products.get('nextPage') or None) if rows else None


async def _fetch_impact(leaf: dict[str, Any], cfg: ProductAutomationSettings, keyword: str, page: int) -> tuple[list[Candidate], int]:
    """One Impact page (offset based). Returns (candidates, raw_row_count)."""
    creds = platform_auth_service.get_credentials('impact')
    if not creds:
        raise RuntimeError('Impact credentials are not configured.')
    campaign_ids = cfg.impact_campaign_ids or [c['id'] for c in await sources.list_impact_campaigns()]
    if not campaign_ids:
        return [], 0
    payload = await run_with_retry(
        step='impact_item_search',
        operation=lambda: ImpactCampaignService.search_items(
            account_sid=creds['account_sid'],
            auth_token=creds['auth_token'],
            keyword=_clean_keyword(keyword),
            limit=FETCH_LIMIT,
            offset=page * FETCH_LIMIT,
            sort_by='DiscountPercentage',
            sort_order='DESC',
            min_discount=cfg.min_discount,
            min_price=cfg.min_price,
            max_price=cfg.max_price,
            campaign_ids=campaign_ids,
        ),
    )
    rows = payload.get('Items') or []
    return [c for r in rows if (c := normalize_impact(r, leaf))], len(rows)


async def fetch_candidates(
    leaf: dict[str, Any],
    cfg: ProductAutomationSettings,
    page: int = 0,
    *,
    state: dict[str, Any] | None = None,
    keyword: str | None = None,
) -> tuple[list[Candidate], list[str]]:
    """Next page of deals for `keyword` (default: the category name) from CJ and Impact.

    Pass the same `state` dict on every call for one keyword: it holds CJ's page cursor and which sources are used
    up. Returns ([], []) once both are exhausted. One platform failing does not drop the other's results.
    """
    kw = keyword or leaf['name']
    st = state if state is not None else {}
    n = st.setdefault('n', page)
    tasks: list[tuple[str, Any]] = []
    if cfg.use_cj and not st.get('cj_done'):
        tasks.append(('cj', _fetch_cj(leaf, cfg, kw, st.get('cj_cursor'))))
    if cfg.use_impact and not st.get('impact_done'):
        tasks.append(('impact', _fetch_impact(leaf, cfg, kw, n)))
    results = await asyncio.gather(*(t[1] for t in tasks), return_exceptions=True)
    candidates: list[Candidate] = []
    errors: list[str] = []
    for (name, _), res in zip(tasks, results):
        if isinstance(res, BaseException):
            errors.append(f'{name}: {res.__class__.__name__}: {str(res)[:200]}')
            logger.warning('automation_source_failed', extra={'source': name, 'category': leaf['id']})
            st[f'{name}_done'] = True  # do not keep hammering a source that errors (e.g. no more pages)
        elif name == 'cj':
            cands, nxt = res
            candidates.extend(cands)
            st['cj_cursor'] = nxt
            if not nxt:
                st['cj_done'] = True
        else:
            cands, raw = res
            candidates.extend(cands)
            if raw < FETCH_LIMIT:
                st['impact_done'] = True
    st['n'] = n + 1
    return candidates, errors

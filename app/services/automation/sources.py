"""Live CJ advertiser / Impact campaign lists (same APIs the affiliate poster uses)."""
from __future__ import annotations

import time
from typing import Any

from app.core.config import get_settings
from app.services.cj.advertiser_lookup_service import CJAdvertiserLookupService
from app.services.impact.campaign_service import ImpactCampaignService
from app.services.platform_auth_service import platform_auth_service

_CACHE_TTL = 3600.0
_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}


def _cached(key: str) -> list[dict[str, str]] | None:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    return None


def _store(key: str, value: list[dict[str, str]]) -> list[dict[str, str]]:
    _cache[key] = (time.monotonic(), value)
    return value


async def list_cj_advertisers(*, force: bool = False) -> list[dict[str, str]]:
    if not force and (hit := _cached('cj')) is not None:
        return hit
    token = platform_auth_service.get_token('cj')
    if not token:
        raise RuntimeError('CJ token is not configured.')
    payload = await CJAdvertiserLookupService.fetch_advertisers(
        bearer_token=token, requestor_cid=get_settings().CJ_COMPANY_ID, advertiser_ids='joined'
    )
    if 'xml' in payload:
        data: Any = CJAdvertiserLookupService.xml_to_json(payload['xml'])
    else:
        data = payload.get('json') or {}
    raw = (data.get('cj-api') or {}).get('advertisers', {}) or {}
    items = raw.get('advertiser') if isinstance(raw, dict) else None
    items = items if isinstance(items, list) else ([items] if items else [])
    out = [
        {'id': str(i.get('advertiser-id', '')).strip(), 'name': str(i.get('advertiser-name', '')).strip()}
        for i in items
        if i.get('advertiser-id')
    ]
    out.sort(key=lambda x: x['name'].lower())
    return _store('cj', out)


async def list_impact_campaigns(*, force: bool = False) -> list[dict[str, str]]:
    if not force and (hit := _cached('impact')) is not None:
        return hit
    creds = platform_auth_service.get_credentials('impact')
    if not creds:
        raise RuntimeError('Impact credentials are not configured.')
    by_id: dict[str, str] = {}
    offset = 0
    limit = 100
    while True:
        payload = await ImpactCampaignService.fetch_campaigns(
            account_sid=creds['account_sid'], auth_token=creds['auth_token'], limit=limit, offset=offset
        )
        campaigns = payload.get('Campaigns') or []
        for c in campaigns:
            # Expired contracts still list but their catalogs are gone (same rule as the affiliate poster UI).
            if str(c.get('ContractStatus', '')).strip().lower() != 'active':
                continue
            cid, name = str(c.get('CampaignId', '')).strip(), str(c.get('CampaignName', '')).strip()
            if cid and name:
                by_id[cid] = name
        if len(campaigns) < limit:
            break
        offset += limit
    out = sorted(({'id': k, 'name': v} for k, v in by_id.items()), key=lambda x: x['name'].lower())
    return _store('impact', out)

import asyncio
from typing import Any

import httpx


class ImpactCampaignService:
    base_url_template = 'https://api.impact.com/Mediapartners/{account_sid}'
    transient_status_codes = {429, 500, 502, 503, 504}

    @classmethod
    async def _request(
        cls,
        *,
        account_sid: str,
        auth_token: str,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{cls.base_url_template.format(account_sid=account_sid)}{path}"
        headers = {'Accept': 'application/json'}

        max_attempts = 3 if method.upper() == 'GET' else 1
        for attempt in range(max_attempts):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.request(
                        method,
                        url,
                        headers=headers,
                        auth=(account_sid, auth_token),
                        params=params,
                        data=data,
                    )
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as exc:
                should_retry = (
                    method.upper() == 'GET'
                    and exc.response.status_code in cls.transient_status_codes
                    and attempt < max_attempts - 1
                )
                if should_retry:
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                raise
            except httpx.RequestError:
                should_retry = method.upper() == 'GET' and attempt < max_attempts - 1
                if should_retry:
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                raise

        raise RuntimeError('Unexpected retry flow termination in ImpactCampaignService._request')

    @classmethod
    def _build_pagination_params(
        cls,
        *,
        limit: int | None,
        offset: int | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if limit is None or offset is None:
            return params
        page = (offset // limit) + 1
        params['PageSize'] = limit
        params['Page'] = page
        return params

    @classmethod
    async def fetch_campaigns(
        cls,
        *,
        account_sid: str,
        auth_token: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        params = cls._build_pagination_params(limit=limit, offset=offset)
        return await cls._request(
            account_sid=account_sid,
            auth_token=auth_token,
            method='GET',
            path='/Campaigns',
            params=params or None,
        )

    @classmethod
    async def fetch_campaign_by_id(
        cls,
        *,
        account_sid: str,
        auth_token: str,
        campaign_id: str,
    ) -> dict[str, Any]:
        return await cls._request(
            account_sid=account_sid,
            auth_token=auth_token,
            method='GET',
            path=f'/Campaigns/{campaign_id}',
        )

    @classmethod
    async def fetch_catalogs(
        cls,
        *,
        account_sid: str,
        auth_token: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        params = cls._build_pagination_params(limit=limit, offset=offset)
        return await cls._request(
            account_sid=account_sid,
            auth_token=auth_token,
            method='GET',
            path='/Catalogs',
            params=params or None,
        )

    @classmethod
    async def fetch_catalog_items(
        cls,
        *,
        account_sid: str,
        auth_token: str,
        catalog_id: str,
        keyword: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        params = cls._build_pagination_params(limit=limit, offset=offset)
        if keyword:
            params['keyword'] = keyword
        return await cls._request(
            account_sid=account_sid,
            auth_token=auth_token,
            method='GET',
            path=f'/Catalogs/{catalog_id}/Items',
            params=params or None,
        )

    @classmethod
    async def search_items(
        cls,
        *,
        account_sid: str,
        auth_token: str,
        keyword: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        params = cls._build_pagination_params(limit=limit, offset=offset)
        if keyword:
            params['keyword'] = keyword
        return await cls._request(
            account_sid=account_sid,
            auth_token=auth_token,
            method='GET',
            path='/Catalogs/ItemSearch',
            params=params or None,
        )

    @classmethod
    async def fetch_media_properties(
        cls,
        *,
        account_sid: str,
        auth_token: str,
    ) -> dict[str, Any]:
        return await cls._request(
            account_sid=account_sid,
            auth_token=auth_token,
            method='GET',
            path='/MediaProperties',
        )

    @classmethod
    async def create_tracking_link(
        cls,
        *,
        account_sid: str,
        auth_token: str,
        program_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await cls._request(
            account_sid=account_sid,
            auth_token=auth_token,
            method='POST',
            path=f'/Programs/{program_id}/TrackingLinks',
            data=payload,
        )

    @classmethod
    async def fetch_deals(
        cls,
        *,
        account_sid: str,
        auth_token: str,
        campaign_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        params = cls._build_pagination_params(limit=limit, offset=offset)
        return await cls._request(
            account_sid=account_sid,
            auth_token=auth_token,
            method='GET',
            path=f'/Campaigns/{campaign_id}/Deals',
            params=params or None,
        )

    @classmethod
    async def fetch_deal_by_id(
        cls,
        *,
        account_sid: str,
        auth_token: str,
        campaign_id: str,
        deal_id: str,
    ) -> dict[str, Any]:
        return await cls._request(
            account_sid=account_sid,
            auth_token=auth_token,
            method='GET',
            path=f'/Campaigns/{campaign_id}/Deals/{deal_id}',
        )

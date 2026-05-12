from typing import Any

import httpx


class ImpactCampaignService:
    base_url_template = 'https://api.impact.com/Mediapartners/{account_sid}'

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

    @classmethod
    async def fetch_campaigns(
        cls,
        *,
        account_sid: str,
        auth_token: str,
    ) -> dict[str, Any]:
        return await cls._request(
            account_sid=account_sid,
            auth_token=auth_token,
            method='GET',
            path='/Campaigns',
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
    ) -> dict[str, Any]:
        return await cls._request(
            account_sid=account_sid,
            auth_token=auth_token,
            method='GET',
            path='/Catalogs',
        )

    @classmethod
    async def fetch_catalog_items(
        cls,
        *,
        account_sid: str,
        auth_token: str,
        catalog_id: str,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
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
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
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
    ) -> dict[str, Any]:
        return await cls._request(
            account_sid=account_sid,
            auth_token=auth_token,
            method='GET',
            path=f'/Campaigns/{campaign_id}/Deals',
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

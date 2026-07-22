import asyncio
from typing import Any

import httpx

from app.utils.pipeline_errors import raise_pipeline_error


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


class ImpactCampaignService:
    base_url_template = 'https://api.impact.com/Mediapartners/{account_sid}'
    transient_status_codes = {429, 500, 502, 503, 504}

    # Impact 400s on unknown SortBy fields with an opaque error, so only forward known-good values.
    sortable_fields = (
        'DiscountPercentage',
        'CurrentPrice',
        'Name',
        'CatalogItemId',
        'Category',
        'Manufacturer',
    )
    sort_orders = ('ASC', 'DESC')

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
        after_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if limit is not None:
            params['PageSize'] = limit
        if after_id:
            params['AfterId'] = after_id
            return params
        if limit is None or offset is None:
            return params
        page = (offset // limit) + 1
        params['Page'] = page
        return params

    @classmethod
    def _build_filter_params(
        cls,
        *,
        sort_by: str | None = None,
        sort_order: str | None = None,
        min_discount: float | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        step: str = 'impact_item_search',
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}

        if sort_by is not None:
            canonical = {field.lower(): field for field in cls.sortable_fields}.get(sort_by.strip().lower())
            if canonical is None:
                raise_pipeline_error(
                    status_code=422,
                    code='INVALID_SORT_FIELD',
                    message=f"Unsupported sortBy value. Allowed: {', '.join(cls.sortable_fields)}.",
                    retryable=False,
                    step=step,
                )
            params['SortBy'] = canonical

        if sort_order is not None:
            normalized_order = sort_order.strip().upper()
            if normalized_order not in cls.sort_orders:
                raise_pipeline_error(
                    status_code=422,
                    code='INVALID_SORT_ORDER',
                    message=f"Unsupported sortOrder value. Allowed: {', '.join(cls.sort_orders)}.",
                    retryable=False,
                    step=step,
                )
            params['SortOrder'] = normalized_order

        # Impact's Query parser only accepts numeric conditions. String conditions such as
        # `Name~boots` or `StockAvailability=InStock` fail upstream with
        # {"Status":"ERROR","Message":"Failed to parse expression"}, so text matching stays on `keyword`.
        conditions: list[str] = []
        if min_discount is not None:
            conditions.append(f'DiscountPercentage>{_format_number(min_discount)}')
        if min_price is not None:
            conditions.append(f'CurrentPrice>{_format_number(min_price)}')
        if max_price is not None:
            conditions.append(f'CurrentPrice<{_format_number(max_price)}')
        if conditions:
            params['Query'] = ' AND '.join(conditions)

        # No promotions filter is exposed: `PromotionIds=!=null` 400s on /Catalogs/ItemSearch
        # ("Invalid search param(s): PromotionIds") and is silently ignored on /Catalogs/{id}/Items
        # (identical @total to an unrecognised param), while `Query=PromotionIds!=null` returns
        # zero rows with @total=-1. There is no working server-side form.

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
        after_id: str | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        min_discount: float | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
    ) -> dict[str, Any]:
        params = cls._build_pagination_params(limit=limit, offset=offset, after_id=after_id)
        if keyword:
            params['keyword'] = keyword
        params.update(
            cls._build_filter_params(
                sort_by=sort_by,
                sort_order=sort_order,
                min_discount=min_discount,
                min_price=min_price,
                max_price=max_price,
                step='impact_catalog_items_search',
            )
        )
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
        after_id: str | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        min_discount: float | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
    ) -> dict[str, Any]:
        params = cls._build_pagination_params(limit=limit, offset=offset, after_id=after_id)
        if keyword:
            params['keyword'] = keyword
        params.update(
            cls._build_filter_params(
                sort_by=sort_by,
                sort_order=sort_order,
                min_discount=min_discount,
                min_price=min_price,
                max_price=max_price,
                step='impact_item_search',
            )
        )
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

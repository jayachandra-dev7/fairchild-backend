from typing import Any

import httpx

from app.schemas.cj.ads_query import CJAdsProductsQueryRequest


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


class CJAdsQueryService:
    base_url = 'https://ads.api.cj.com/query'

    @classmethod
    async def query_products(
        cls,
        *,
        bearer_token: str,
        request: CJAdsProductsQueryRequest,
    ) -> dict[str, Any]:
        headers = {
            'Authorization': f'Bearer {bearer_token}',
            'Content-Type': 'application/graphql',
        }
        graphql_query = cls._build_products_query(request)

        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(cls.base_url, headers=headers, content=graphql_query)
            response.raise_for_status()
            payload = response.json()

        if request.sort_by:
            # Sorting and cursor pagination are mutually exclusive upstream, so the sorted
            # query cannot ask for nextPage. Report explicitly that there is no further page.
            products = payload.get('data', {}).get('products') if isinstance(payload, dict) else None
            if isinstance(products, dict):
                products['nextPage'] = None

        return payload

    @classmethod
    def _build_products_query(cls, request: CJAdsProductsQueryRequest) -> str:
        query_parts: list[str] = [f'companyId: "{request.company_id}"']

        keywords = [item.strip() for item in request.keywords if item.strip()]
        if keywords:
            keywords_csv = ', '.join(f'"{item}"' for item in keywords)
            query_parts.append(f'keywords: [{keywords_csv}]')

        advertiser_countries = [item.strip().upper() for item in request.advertiser_countries if item.strip()]
        if advertiser_countries:
            countries_csv = ', '.join(f'"{item}"' for item in advertiser_countries)
            query_parts.append(f'advertiserCountries: [{countries_csv}]')

        if request.availability.strip():
            query_parts.append(f'availability: {request.availability.strip()}')

        if request.partner_status.strip():
            query_parts.append(f'partnerStatus: {request.partner_status.strip()}')

        partner_ids = [item.strip() for item in request.partner_ids if item.strip()]
        if partner_ids:
            partner_ids_csv = ', '.join(f'"{item}"' for item in partner_ids)
            query_parts.append(f'partnerIds: [{partner_ids_csv}]')

        if request.discount_percentage is not None:
            query_parts.append(f'discountPercentage: {_format_number(request.discount_percentage)}')

        if request.low_price is not None:
            query_parts.append(f'lowPrice: {_format_number(request.low_price)}')

        if request.high_price is not None:
            query_parts.append(f'highPrice: {_format_number(request.high_price)}')

        if request.brand and request.brand.strip():
            query_parts.append(f'brand: "{request.brand.strip()}"')

        if request.sort_by:
            query_parts.append(f'sortBy: {request.sort_by}')

        if request.sort_order:
            query_parts.append(f'sortOrder: {request.sort_order}')

        query_parts.append(f'limit: {request.limit}')
        if request.page and request.page.strip():
            query_parts.append(f'page: "{request.page.strip()}"')
        else:
            query_parts.append(f'offset: {request.offset}')
        args_block = ', '.join(query_parts)

        # CJ 400s on `nextPage` appearing anywhere in the selection set once sorting is requested,
        # regardless of the arguments used, so drop the field entirely when sorting.
        page_fields = 'totalCount count limit ' if request.sort_by else 'totalCount count limit nextPage '

        return (
            '{ products('
            f'{args_block}'
            ') { '
            f'{page_fields}'
            'resultList { '
            'advertiserId catalogId advertiserName id title description imageLink link '
            'price { amount currency } '
            'salePrice { amount currency } '
            f'linkCode(pid: "{request.pid}") {{ clickUrl imageUrl }} '
            '... on Shopping { availability } '
            '} '
            '} }'
        )

from typing import Any

import httpx

from app.schemas.cj.ads_query import CJAdsProductsQueryRequest


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
            return response.json()

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

        query_parts.append(f'limit: {request.limit}')
        query_parts.append(f'offset: {request.offset}')
        args_block = ', '.join(query_parts)

        return (
            '{ products('
            f'{args_block}'
            ') { '
            'totalCount count limit nextPage '
            'resultList { '
            'advertiserId catalogId id title description imageLink link '
            'price { amount currency } '
            'salePrice { amount currency } '
            f'linkCode(pid: "{request.pid}") {{ clickUrl imageUrl }} '
            '... on Shopping { availability } '
            '} '
            '} }'
        )

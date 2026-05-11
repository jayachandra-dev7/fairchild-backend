from fastapi import APIRouter
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from httpx import HTTPError, HTTPStatusError

from app.schemas.cj.ads_query import CJAdsProductsQueryRequest
from app.schemas.common import ApiResponse, ErrorDetail
from app.schemas.platform_auth import PlatformAuthorizeRequest, PlatformAuthorizeResponse
from app.services.cj.ads_query_service import CJAdsQueryService
from app.services.cj.advertiser_lookup_service import CJAdvertiserLookupService
from app.services.platform_auth_service import platform_auth_service

router = APIRouter(prefix='/cj', tags=['CJ'])
cj_bearer = HTTPBearer(
    auto_error=False,
    scheme_name='CJBearerAuth',
    description='Bearer token for CJ APIs only',
)


@router.get('/health')
def cj_health() -> dict[str, str]:
    return {'platform': 'cj', 'status': 'ready'}


@router.post('/authorize', response_model=ApiResponse[PlatformAuthorizeResponse])
def cj_authorize(body: PlatformAuthorizeRequest) -> ApiResponse[PlatformAuthorizeResponse]:
    platform_auth_service.set_token('cj', body.token)
    return ApiResponse(
        data=PlatformAuthorizeResponse(
            platform='cj',
            authorized=True,
            message='CJ token saved successfully',
        )
    )


@router.get('/advertisers/lookup', response_model=ApiResponse[dict])
async def advertiser_lookup(
    requestor_cid: str = Query(alias='requestor-cid', min_length=1),
    advertiser_ids: str = Query(default='joined', alias='advertiser-ids', min_length=1),
    response_format: str = Query(default='json', alias='response-format', pattern='^(json|raw)$'),
    credentials: HTTPAuthorizationCredentials | None = Depends(cj_bearer),
) -> ApiResponse[dict]:
    bearer_token = ''
    if credentials is not None and credentials.scheme.lower() == 'bearer':
        bearer_token = credentials.credentials.strip()
    else:
        saved_token = platform_auth_service.get_token('cj')
        if saved_token:
            bearer_token = saved_token

    if not bearer_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorDetail(
                code='MISSING_AUTHORIZATION',
                message='Provide Bearer token or set once via POST /api/v1/cj/authorize',
            ).model_dump(),
        )

    normalized_advertiser_ids = advertiser_ids.strip()
    if not normalized_advertiser_ids:
        normalized_advertiser_ids = 'joined'

    if normalized_advertiser_ids.lower() != 'joined':
        id_parts = [item.strip() for item in normalized_advertiser_ids.split(',')]
        if not id_parts or any(not item for item in id_parts):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ErrorDetail(
                    code='INVALID_ADVERTISER_IDS',
                    message='advertiser-ids must be joined or comma-separated IDs',
                ).model_dump(),
            )
        normalized_advertiser_ids = ','.join(id_parts)

    try:
        payload = await CJAdvertiserLookupService.fetch_advertisers(
            bearer_token=bearer_token,
            requestor_cid=requestor_cid,
            advertiser_ids=normalized_advertiser_ids,
        )
        if response_format == 'json':
            if 'json' in payload:
                return ApiResponse(data=payload['json'])
            if 'xml' in payload:
                parsed = CJAdvertiserLookupService.xml_to_json(payload['xml'])
                return ApiResponse(data=parsed)
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        message = f'CJ API error: {exc.response.status_code}'
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='CJ_API_ERROR',
                message=message,
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='CJ_CONNECTIVITY_ERROR',
                message='Unable to reach CJ advertiser lookup API',
            ).model_dump(),
        ) from exc


@router.post('/ads/products/query', response_model=ApiResponse[dict])
async def query_cj_products(body: CJAdsProductsQueryRequest) -> ApiResponse[dict]:
    bearer_token = platform_auth_service.get_token('cj')
    if not bearer_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorDetail(
                code='MISSING_AUTHORIZATION',
                message='Set CJ token first via POST /api/v1/cj/authorize',
            ).model_dump(),
        )

    try:
        payload = await CJAdsQueryService.query_products(
            bearer_token=bearer_token,
            request=body,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='CJ_ADS_API_ERROR',
                message=f'CJ Ads API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='CJ_ADS_CONNECTIVITY_ERROR',
                message='Unable to reach CJ Ads API',
            ).model_dump(),
        ) from exc


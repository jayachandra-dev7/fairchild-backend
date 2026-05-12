from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Query
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from httpx import HTTPError, HTTPStatusError

from app.schemas.common import ApiResponse, ErrorDetail
from app.schemas.impact.auth import ImpactAuthorizeRequest, ImpactAuthorizeResponse
from app.schemas.impact.tracking_link import ImpactTrackingLinkCreateRequest
from app.services.impact.campaign_service import ImpactCampaignService
from app.services.platform_auth_service import platform_auth_service

router = APIRouter(prefix='/impact', tags=['Impact'])
impact_basic = HTTPBasic(auto_error=False)


@router.get('/health')
def impact_health() -> dict[str, str]:
    return {'platform': 'impact', 'status': 'ready'}


def _resolve_impact_credentials(
    credentials: HTTPBasicCredentials | None,
) -> tuple[str, str]:
    if credentials is not None and credentials.username.strip() and credentials.password.strip():
        return credentials.username.strip(), credentials.password.strip()

    saved_credentials = platform_auth_service.get_credentials('impact')
    if saved_credentials:
        return saved_credentials['account_sid'], saved_credentials['auth_token']

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=ErrorDetail(
            code='MISSING_AUTHORIZATION',
            message='Provide Impact Basic Auth or set credentials first via POST /api/v1/impact/authorize',
        ).model_dump(),
    )


@router.post('/authorize', response_model=ApiResponse[ImpactAuthorizeResponse])
def impact_authorize(body: ImpactAuthorizeRequest) -> ApiResponse[ImpactAuthorizeResponse]:
    platform_auth_service.set_credentials(
        'impact',
        {
            'account_sid': body.account_sid,
            'auth_token': body.auth_token,
        },
    )
    return ApiResponse(
        data=ImpactAuthorizeResponse(
            platform='impact',
            account_sid=body.account_sid,
            authorized=True,
            message='Impact credentials saved successfully',
        )
    )


@router.get('/campaigns', response_model=ApiResponse[dict])
async def get_impact_campaigns(
    credentials: HTTPBasicCredentials | None = Depends(impact_basic),
) -> ApiResponse[dict]:
    account_sid, auth_token = _resolve_impact_credentials(credentials)

    try:
        payload = await ImpactCampaignService.fetch_campaigns(
            account_sid=account_sid,
            auth_token=auth_token,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='IMPACT_API_ERROR',
                message=f'Impact API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='IMPACT_CONNECTIVITY_ERROR',
                message='Unable to reach Impact API',
            ).model_dump(),
        ) from exc


@router.get('/campaigns/{campaign_id}', response_model=ApiResponse[dict])
async def get_impact_campaign_by_id(
    campaign_id: str,
    credentials: HTTPBasicCredentials | None = Depends(impact_basic),
) -> ApiResponse[dict]:
    account_sid, auth_token = _resolve_impact_credentials(credentials)

    try:
        payload = await ImpactCampaignService.fetch_campaign_by_id(
            account_sid=account_sid,
            auth_token=auth_token,
            campaign_id=campaign_id,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='IMPACT_API_ERROR',
                message=f'Impact API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='IMPACT_CONNECTIVITY_ERROR',
                message='Unable to reach Impact API',
            ).model_dump(),
        ) from exc


@router.get('/campaigns/{campaign_id}/deals', response_model=ApiResponse[dict])
async def get_impact_deals(
    campaign_id: str,
    credentials: HTTPBasicCredentials | None = Depends(impact_basic),
) -> ApiResponse[dict]:
    account_sid, auth_token = _resolve_impact_credentials(credentials)

    try:
        payload = await ImpactCampaignService.fetch_deals(
            account_sid=account_sid,
            auth_token=auth_token,
            campaign_id=campaign_id,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='IMPACT_API_ERROR',
                message=f'Impact API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='IMPACT_CONNECTIVITY_ERROR',
                message='Unable to reach Impact API',
            ).model_dump(),
        ) from exc


@router.get('/campaigns/{campaign_id}/deals/{deal_id}', response_model=ApiResponse[dict])
async def get_impact_deal_by_id(
    campaign_id: str,
    deal_id: str,
    credentials: HTTPBasicCredentials | None = Depends(impact_basic),
) -> ApiResponse[dict]:
    account_sid, auth_token = _resolve_impact_credentials(credentials)

    try:
        payload = await ImpactCampaignService.fetch_deal_by_id(
            account_sid=account_sid,
            auth_token=auth_token,
            campaign_id=campaign_id,
            deal_id=deal_id,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='IMPACT_API_ERROR',
                message=f'Impact API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='IMPACT_CONNECTIVITY_ERROR',
                message='Unable to reach Impact API',
            ).model_dump(),
        ) from exc


@router.get('/catalogs', response_model=ApiResponse[dict])
async def get_impact_catalogs(
    credentials: HTTPBasicCredentials | None = Depends(impact_basic),
) -> ApiResponse[dict]:
    account_sid, auth_token = _resolve_impact_credentials(credentials)

    try:
        payload = await ImpactCampaignService.fetch_catalogs(
            account_sid=account_sid,
            auth_token=auth_token,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='IMPACT_API_ERROR',
                message=f'Impact API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='IMPACT_CONNECTIVITY_ERROR',
                message='Unable to reach Impact API',
            ).model_dump(),
        ) from exc


@router.get('/catalogs/{catalog_id}/items', response_model=ApiResponse[dict])
async def get_impact_catalog_items(
    catalog_id: str,
    keyword: str | None = Query(default=None),
    credentials: HTTPBasicCredentials | None = Depends(impact_basic),
) -> ApiResponse[dict]:
    account_sid, auth_token = _resolve_impact_credentials(credentials)

    try:
        payload = await ImpactCampaignService.fetch_catalog_items(
            account_sid=account_sid,
            auth_token=auth_token,
            catalog_id=catalog_id,
            keyword=keyword,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='IMPACT_API_ERROR',
                message=f'Impact API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='IMPACT_CONNECTIVITY_ERROR',
                message='Unable to reach Impact API',
            ).model_dump(),
        ) from exc


@router.get('/catalogs/item-search', response_model=ApiResponse[dict])
async def search_impact_items(
    keyword: str | None = Query(default=None),
    credentials: HTTPBasicCredentials | None = Depends(impact_basic),
) -> ApiResponse[dict]:
    account_sid, auth_token = _resolve_impact_credentials(credentials)

    try:
        payload = await ImpactCampaignService.search_items(
            account_sid=account_sid,
            auth_token=auth_token,
            keyword=keyword,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='IMPACT_API_ERROR',
                message=f'Impact API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='IMPACT_CONNECTIVITY_ERROR',
                message='Unable to reach Impact API',
            ).model_dump(),
        ) from exc


@router.get('/media-properties', response_model=ApiResponse[dict])
async def get_impact_media_properties(
    credentials: HTTPBasicCredentials | None = Depends(impact_basic),
) -> ApiResponse[dict]:
    account_sid, auth_token = _resolve_impact_credentials(credentials)

    try:
        payload = await ImpactCampaignService.fetch_media_properties(
            account_sid=account_sid,
            auth_token=auth_token,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='IMPACT_API_ERROR',
                message=f'Impact API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='IMPACT_CONNECTIVITY_ERROR',
                message='Unable to reach Impact API',
            ).model_dump(),
        ) from exc


@router.post('/programs/{program_id}/tracking-links', response_model=ApiResponse[dict])
async def create_impact_tracking_link(
    program_id: str,
    body: ImpactTrackingLinkCreateRequest,
    credentials: HTTPBasicCredentials | None = Depends(impact_basic),
) -> ApiResponse[dict]:
    account_sid, auth_token = _resolve_impact_credentials(credentials)
    payload = {
        'Deeplink': body.deeplink,
        'SharedId': body.shared_id,
        'SubId1': body.sub_id1,
        'SubId2': body.sub_id2,
        'SubId3': body.sub_id3,
        'SubId4': body.sub_id4,
        'MediaPartnerPropertyId': body.media_partner_property_id,
    }
    filtered_payload = {key: value for key, value in payload.items() if value not in (None, '')}

    try:
        result = await ImpactCampaignService.create_tracking_link(
            account_sid=account_sid,
            auth_token=auth_token,
            program_id=program_id,
            payload=filtered_payload,
        )
        return ApiResponse(data=result)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='IMPACT_API_ERROR',
                message=f'Impact API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='IMPACT_CONNECTIVITY_ERROR',
                message='Unable to reach Impact API',
            ).model_dump(),
        ) from exc

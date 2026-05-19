from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from httpx import HTTPError, HTTPStatusError

from app.core.config import get_settings
from app.schemas.common import ApiResponse, ErrorDetail
from app.schemas.metricool.auth import MetricoolAuthorizeRequest, MetricoolAuthorizeResponse
from app.schemas.metricool.post import MetricoolSchedulerPostRequest
from app.services.metricool.service import MetricoolService
from app.services.platform_auth_service import platform_auth_service

router = APIRouter(prefix='/metricool', tags=['Metricool'])
settings = get_settings()


@router.get('/health')
def metricool_health() -> dict[str, str]:
    return {'platform': 'metricool', 'status': 'ready'}


def _resolve_metricool_token() -> str:
    token = platform_auth_service.get_token('metricool')
    if token:
        return token
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=ErrorDetail(
            code='MISSING_AUTHORIZATION',
            message='Set Metricool token first via POST /api/v1/metricool/authorize',
        ).model_dump(),
    )


@router.post('/authorize', response_model=ApiResponse[MetricoolAuthorizeResponse])
def metricool_authorize(body: MetricoolAuthorizeRequest) -> ApiResponse[MetricoolAuthorizeResponse]:
    platform_auth_service.set_token('metricool', body.token)
    return ApiResponse(
        data=MetricoolAuthorizeResponse(
            platform='metricool',
            authorized=True,
            message='Metricool token saved successfully',
        )
    )


@router.get('/profiles', response_model=ApiResponse[Any])
async def metricool_profiles() -> ApiResponse[Any]:
    token = _resolve_metricool_token()

    try:
        payload = await MetricoolService.get_simple_profiles(token=token)
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='METRICOOL_API_ERROR',
                message=f'Metricool API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='METRICOOL_CONNECTIVITY_ERROR',
                message='Unable to reach Metricool API',
            ).model_dump(),
        ) from exc


@router.post('/upload', response_model=ApiResponse[Any])
async def metricool_upload(
    user_id: str = Query(alias='userId', min_length=1),
    blog_id: str = Query(alias='blogId', min_length=1),
    picture: UploadFile = File(...),
) -> ApiResponse[Any]:
    token = _resolve_metricool_token()

    try:
        payload = await MetricoolService.upload_media(
            token=token,
            user_id=user_id,
            blog_id=blog_id,
            picture=picture,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='METRICOOL_API_ERROR',
                message=f'Metricool API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='METRICOOL_CONNECTIVITY_ERROR',
                message='Unable to reach Metricool API',
            ).model_dump(),
        ) from exc


@router.post('/scheduler/posts', response_model=ApiResponse[Any])
async def metricool_create_scheduler_post(
    body: MetricoolSchedulerPostRequest,
    user_id: str = Query(alias='userId', min_length=1),
    blog_id: str = Query(alias='blogId', min_length=1),
) -> ApiResponse[Any]:
    token = _resolve_metricool_token()

    try:
        payload = await MetricoolService.create_scheduler_post(
            token=token,
            user_id=user_id,
            blog_id=blog_id,
            payload=body,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='METRICOOL_API_ERROR',
                message=f'Metricool API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='METRICOOL_CONNECTIVITY_ERROR',
                message='Unable to reach Metricool API',
            ).model_dump(),
        ) from exc


@router.get('/scheduler/posts', response_model=ApiResponse[Any])
async def metricool_get_scheduler_posts(
    start: str = Query(min_length=1),
    end: str = Query(min_length=1),
    user_id: str | None = Query(default=None, alias='userId'),
    blog_id: str | None = Query(default=None, alias='blogId'),
    timezone: str = Query(default='America/Denver'),
    extended_range: bool = Query(default=True, alias='extendedRange'),
) -> ApiResponse[Any]:
    token = _resolve_metricool_token()
    resolved_user_id = user_id or settings.METRICOOL_USER_ID
    resolved_blog_id = blog_id or settings.METRICOOL_BLOG_ID
    if not resolved_user_id or not resolved_blog_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorDetail(
                code='MISSING_METRICOOL_IDS',
                message='Provide userId/blogId or set METRICOOL_USER_ID and METRICOOL_BLOG_ID in .env',
            ).model_dump(),
        )

    try:
        payload = await MetricoolService.get_scheduler_posts(
            token=token,
            user_id=resolved_user_id,
            blog_id=resolved_blog_id,
            start=start,
            end=end,
            timezone=timezone,
            extended_range=extended_range,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='METRICOOL_API_ERROR',
                message=f'Metricool API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='METRICOOL_CONNECTIVITY_ERROR',
                message='Unable to reach Metricool API',
            ).model_dump(),
        ) from exc


@router.get('/scheduler/boards/pinterest', response_model=ApiResponse[Any])
async def metricool_get_pinterest_boards(
    user_id: str | None = Query(default=None, alias='userId'),
    blog_id: str | None = Query(default=None, alias='blogId'),
) -> ApiResponse[Any]:
    token = _resolve_metricool_token()
    resolved_user_id = user_id or settings.METRICOOL_USER_ID
    resolved_blog_id = blog_id or settings.METRICOOL_BLOG_ID
    if not resolved_user_id or not resolved_blog_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorDetail(
                code='MISSING_METRICOOL_IDS',
                message='Provide userId/blogId or set METRICOOL_USER_ID and METRICOOL_BLOG_ID in .env',
            ).model_dump(),
        )

    try:
        payload = await MetricoolService.get_pinterest_boards(
            token=token,
            user_id=resolved_user_id,
            blog_id=resolved_blog_id,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='METRICOOL_API_ERROR',
                message=f'Metricool API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='METRICOOL_CONNECTIVITY_ERROR',
                message='Unable to reach Metricool API',
            ).model_dump(),
        ) from exc

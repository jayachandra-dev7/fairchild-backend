from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from httpx import HTTPError, HTTPStatusError

from app.schemas.common import ApiResponse, ErrorDetail
from app.schemas.renderform.auth import RenderFormAuthorizeRequest, RenderFormAuthorizeResponse
from app.schemas.renderform.render import RenderFormRenderRequest
from app.services.platform_auth_service import platform_auth_service
from app.services.renderform.service import RenderFormService

router = APIRouter(prefix='/renderform', tags=['RenderForm'])


@router.get('/health')
def renderform_health() -> dict[str, str]:
    return {'platform': 'renderform', 'status': 'ready'}


def _resolve_renderform_api_key() -> str:
    token = platform_auth_service.get_token('renderform')
    if token:
        return token
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=ErrorDetail(
            code='MISSING_AUTHORIZATION',
            message='Set RenderForm API key first via POST /api/v1/renderform/authorize',
        ).model_dump(),
    )


@router.post('/authorize', response_model=ApiResponse[RenderFormAuthorizeResponse])
def renderform_authorize(body: RenderFormAuthorizeRequest) -> ApiResponse[RenderFormAuthorizeResponse]:
    platform_auth_service.set_token('renderform', body.api_key)
    return ApiResponse(
        data=RenderFormAuthorizeResponse(
            platform='renderform',
            authorized=True,
            message='RenderForm API key saved successfully',
        )
    )


@router.get('/templates', response_model=ApiResponse[Any])
async def renderform_templates() -> ApiResponse[Any]:
    api_key = _resolve_renderform_api_key()

    try:
        payload = await RenderFormService.list_templates(api_key=api_key)
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='RENDERFORM_API_ERROR',
                message=f'RenderForm API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='RENDERFORM_CONNECTIVITY_ERROR',
                message='Unable to reach RenderForm API',
            ).model_dump(),
        ) from exc


@router.post('/render', response_model=ApiResponse[Any])
async def renderform_render(
    body: RenderFormRenderRequest,
) -> ApiResponse[Any]:
    api_key = _resolve_renderform_api_key()
    data = {
        'title.text': body.title_text,
        'image.src': body.image_src,
        **body.extra_data,
    }

    try:
        payload = await RenderFormService.render_template(
            api_key=api_key,
            template=body.template,
            data=data,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='RENDERFORM_API_ERROR',
                message=f'RenderForm API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='RENDERFORM_CONNECTIVITY_ERROR',
                message='Unable to reach RenderForm API',
            ).model_dump(),
        ) from exc


@router.post('/render/upload', response_model=ApiResponse[Any])
async def renderform_render_upload(
    template: str = Form(default='noisy-griffins-play-safely-1558'),
    title_text: str = Form(default='AUTOMATION TEST', alias='titleText'),
    image: UploadFile = File(...),
) -> ApiResponse[Any]:
    api_key = _resolve_renderform_api_key()
    image_bytes = await image.read()
    image_src = RenderFormService.to_data_url(
        file_bytes=image_bytes,
        content_type=image.content_type or 'application/octet-stream',
    )
    data = {
        'title.text': title_text,
        'image.src': image_src,
    }

    try:
        payload = await RenderFormService.render_template(
            api_key=api_key,
            template=template,
            data=data,
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='RENDERFORM_API_ERROR',
                message=f'RenderForm API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='RENDERFORM_CONNECTIVITY_ERROR',
                message='Unable to reach RenderForm API',
            ).model_dump(),
        ) from exc

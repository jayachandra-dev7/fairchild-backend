from typing import Any
import asyncio
import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from httpx import HTTPError, HTTPStatusError

from app.schemas.common import ApiResponse, ErrorDetail
from app.schemas.renderform.auth import RenderFormAuthorizeRequest, RenderFormAuthorizeResponse
from app.schemas.renderform.render import RenderFormRenderRequest
from app.services.platform_auth_service import platform_auth_service
from app.services.renderform.service import RenderFormService
from app.utils.image_validation import ImageValidationError, fetch_and_validate_image_url, validate_image_bytes
from app.utils.pipeline_errors import raise_pipeline_error
from app.utils.retry import run_with_retry

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/renderform', tags=['RenderForm'])
MAX_BLANK_RENDER_RETRIES = 2
BLANK_RENDER_RETRY_DELAYS = [0.7, 1.4]


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
    completed_steps: list[str] = []
    # Source-image pre-validation is a best-effort courtesy check only. RenderForm fetches the
    # image itself, so a failure here (CDN blocking our agent, timeout, transient HTTP error, or
    # a low-variance-but-valid product shot) must NOT fail the request before RenderForm has even
    # been tried. Log and proceed with the original image_src; only RenderForm's own rejection surfaces.
    if body.image_src.strip().lower().startswith(('http://', 'https://')):
        try:
            await fetch_and_validate_image_url(body.image_src.strip())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                'Source image pre-validation failed for %s (%s); proceeding to RenderForm anyway',
                body.image_src.strip(),
                exc.__class__.__name__,
            )

    data = {
        'title.text': body.title_text,
        'image.src': body.image_src,
        **body.extra_data,
    }

    try:
        payload: Any | None = None
        validation_error_details: Any | None = None
        for attempt in range(MAX_BLANK_RENDER_RETRIES + 1):
            payload = await run_with_retry(
                step='renderform_render',
                operation=lambda: RenderFormService.render_template(
                    api_key=api_key,
                    template=body.template,
                    data=data,
                ),
            )
            rendered_image_url = RenderFormService.extract_rendered_image_url(payload)
            if not rendered_image_url:
                break
            try:
                await run_with_retry(
                    step='renderform_render',
                    operation=lambda: fetch_and_validate_image_url(rendered_image_url),
                )
                validation_error_details = None
                break
            except ImageValidationError as exc:
                validation_error_details = {
                    'renderedImageUrl': rendered_image_url,
                    'validation': exc.details,
                    'attempt': attempt + 1,
                }
                if attempt < MAX_BLANK_RENDER_RETRIES:
                    await asyncio.sleep(BLANK_RENDER_RETRY_DELAYS[min(attempt, len(BLANK_RENDER_RETRY_DELAYS) - 1)])
                    continue
                raise_pipeline_error(
                    status_code=422,
                    code='IMAGE_INVALID_OR_BLANK',
                    message='Rendered image is blank/invalid after retries.',
                    details=validation_error_details,
                    retryable=False,
                    step='renderform_render',
                    completed_steps=completed_steps,
                    failed_step='renderform_render',
                )

        if payload is None:
            raise_pipeline_error(
                status_code=502,
                code='RENDER_TIMEOUT',
                message='RenderForm request failed before producing output.',
                details={'reason': 'empty_render_payload'},
                retryable=True,
                step='renderform_render',
                completed_steps=completed_steps,
                failed_step='renderform_render',
                can_retry_from_step='renderform_render',
            )
        completed_steps.append('renderform_render')
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        code = 'UPSTREAM_RATE_LIMITED' if exc.response.status_code == 429 else 'RENDER_TIMEOUT'
        retryable = exc.response.status_code in {429, 500, 502, 503, 504}
        raise_pipeline_error(
            status_code=exc.response.status_code,
            code=code,
            message='RenderForm request failed.',
            details={'status_code': exc.response.status_code},
            retryable=retryable,
            step='renderform_render',
            completed_steps=completed_steps,
            failed_step='renderform_render',
            can_retry_from_step='renderform_render' if retryable else None,
        )
    except HTTPError as exc:
        raise_pipeline_error(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code='RENDER_TIMEOUT',
            message='RenderForm request timed out or failed to connect.',
            details={'error': exc.__class__.__name__},
            retryable=True,
            step='renderform_render',
            completed_steps=completed_steps,
            failed_step='renderform_render',
            can_retry_from_step='renderform_render',
        )


@router.post('/render/upload', response_model=ApiResponse[Any])
async def renderform_render_upload(
    template: str = Form(default='noisy-griffins-play-safely-1558'),
    title_text: str = Form(default='AUTOMATION TEST', alias='titleText'),
    image: UploadFile = File(...),
) -> ApiResponse[Any]:
    api_key = _resolve_renderform_api_key()
    image_bytes = await image.read()
    completed_steps: list[str] = []
    try:
        validate_image_bytes(
            image_bytes=image_bytes,
            content_type=image.content_type or 'application/octet-stream',
        )
    except ImageValidationError as exc:
        raise_pipeline_error(
            status_code=422,
            code='IMAGE_INVALID_OR_BLANK',
            message=exc.message,
            details=exc.details,
            retryable=False,
            step='renderform_render',
            completed_steps=completed_steps,
            failed_step='renderform_render',
        )

    image_src = RenderFormService.to_data_url(
        file_bytes=image_bytes,
        content_type=image.content_type or 'application/octet-stream',
    )
    data = {
        'title.text': title_text,
        'image.src': image_src,
    }

    try:
        payload: Any | None = None
        validation_error_details: Any | None = None
        for attempt in range(MAX_BLANK_RENDER_RETRIES + 1):
            payload = await run_with_retry(
                step='renderform_render',
                operation=lambda: RenderFormService.render_template(
                    api_key=api_key,
                    template=template,
                    data=data,
                ),
            )
            rendered_image_url = RenderFormService.extract_rendered_image_url(payload)
            if not rendered_image_url:
                break
            try:
                await run_with_retry(
                    step='renderform_render',
                    operation=lambda: fetch_and_validate_image_url(rendered_image_url),
                )
                validation_error_details = None
                break
            except ImageValidationError as exc:
                validation_error_details = {
                    'renderedImageUrl': rendered_image_url,
                    'validation': exc.details,
                    'attempt': attempt + 1,
                }
                if attempt < MAX_BLANK_RENDER_RETRIES:
                    await asyncio.sleep(BLANK_RENDER_RETRY_DELAYS[min(attempt, len(BLANK_RENDER_RETRY_DELAYS) - 1)])
                    continue
                raise_pipeline_error(
                    status_code=422,
                    code='IMAGE_INVALID_OR_BLANK',
                    message='Rendered image is blank/invalid after retries.',
                    details=validation_error_details,
                    retryable=False,
                    step='renderform_render',
                    completed_steps=completed_steps,
                    failed_step='renderform_render',
                )

        if payload is None:
            raise_pipeline_error(
                status_code=502,
                code='RENDER_TIMEOUT',
                message='RenderForm request failed before producing output.',
                details={'reason': 'empty_render_payload'},
                retryable=True,
                step='renderform_render',
                completed_steps=completed_steps,
                failed_step='renderform_render',
                can_retry_from_step='renderform_render',
            )
        completed_steps.append('renderform_render')
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        code = 'UPSTREAM_RATE_LIMITED' if exc.response.status_code == 429 else 'RENDER_TIMEOUT'
        retryable = exc.response.status_code in {429, 500, 502, 503, 504}
        raise_pipeline_error(
            status_code=exc.response.status_code,
            code=code,
            message='RenderForm request failed.',
            details={'status_code': exc.response.status_code},
            retryable=retryable,
            step='renderform_render',
            completed_steps=completed_steps,
            failed_step='renderform_render',
            can_retry_from_step='renderform_render' if retryable else None,
        )
    except HTTPError as exc:
        raise_pipeline_error(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code='RENDER_TIMEOUT',
            message='RenderForm request timed out or failed to connect.',
            details={'error': exc.__class__.__name__},
            retryable=True,
            step='renderform_render',
            completed_steps=completed_steps,
            failed_step='renderform_render',
            can_retry_from_step='renderform_render',
        )

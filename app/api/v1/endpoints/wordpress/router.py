from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from httpx import HTTPError, HTTPStatusError

from app.schemas.common import ApiResponse, ErrorDetail
from app.schemas.wordpress.auth import WordPressAuthorizeRequest, WordPressAuthorizeResponse
from app.schemas.wordpress.product import WooProductCreateRequest
from app.services.wordpress.service import WordPressService
from app.services.platform_auth_service import platform_auth_service
from app.utils.image_validation import ImageValidationError, fetch_and_validate_image_url, validate_image_bytes
from app.utils.pipeline_errors import raise_pipeline_error
from app.utils.retry import run_with_retry

router = APIRouter(prefix='/wordpress', tags=['WordPress'])


@router.get('/health')
def wordpress_health() -> dict[str, str]:
    return {'platform': 'wordpress', 'status': 'ready'}


def _resolve_wordpress_credentials() -> dict[str, str]:
    credentials = platform_auth_service.get_credentials('wordpress')
    if credentials:
        return credentials
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=ErrorDetail(
            code='MISSING_AUTHORIZATION',
            message='Set WordPress credentials first via POST /api/v1/wordpress/authorize',
        ).model_dump(),
    )


@router.post('/authorize', response_model=ApiResponse[WordPressAuthorizeResponse])
def wordpress_authorize(body: WordPressAuthorizeRequest) -> ApiResponse[WordPressAuthorizeResponse]:
    platform_auth_service.set_credentials(
        'wordpress',
        {
            'domain': body.domain,
            'wc_consumer_key': body.wc_consumer_key,
            'wc_consumer_secret': body.wc_consumer_secret,
        },
    )
    return ApiResponse(
        data=WordPressAuthorizeResponse(
            platform='wordpress',
            domain=body.domain,
            authorized=True,
            message='WordPress credentials saved successfully',
        )
    )


@router.post('/media/upload', response_model=ApiResponse[Any])
async def wordpress_upload_media(
    file: UploadFile | None = File(default=None),
    image_url: str | None = Form(default=None, alias='image_url'),
) -> ApiResponse[Any]:
    credentials = _resolve_wordpress_credentials()
    completed_steps = ['renderform_render']
    if file is None and not (image_url and image_url.strip()):
        raise_pipeline_error(
            status_code=422,
            code='MEDIA_UPLOAD_INVALID_INPUT',
            message='Provide either file or image_url',
            retryable=False,
            step='wordpress_media_upload',
            completed_steps=completed_steps,
            failed_step='wordpress_media_upload',
        )

    try:
        if image_url and image_url.strip():
            image_bytes, content_type = await run_with_retry(
                step='wordpress_media_upload',
                operation=lambda: fetch_and_validate_image_url(image_url.strip()),
            )
            payload = await run_with_retry(
                step='wordpress_media_upload',
                operation=lambda: WordPressService.upload_media_bytes(
                    domain=credentials['domain'],
                    wc_consumer_key=credentials['wc_consumer_key'],
                    wc_consumer_secret=credentials['wc_consumer_secret'],
                    filename=image_url.strip().split('/')[-1] or 'remote-image',
                    content_type=content_type,
                    file_bytes=image_bytes,
                ),
            )
        else:
            file_bytes = await file.read() if file else b''
            validate_image_bytes(
                image_bytes=file_bytes,
                content_type=file.content_type if file else 'application/octet-stream',
            )
            payload = await run_with_retry(
                step='wordpress_media_upload',
                operation=lambda: WordPressService.upload_media_bytes(
                    domain=credentials['domain'],
                    wc_consumer_key=credentials['wc_consumer_key'],
                    wc_consumer_secret=credentials['wc_consumer_secret'],
                    filename=file.filename if file else 'upload.bin',
                    content_type=file.content_type if file else 'application/octet-stream',
                    file_bytes=file_bytes,
                ),
            )
        completed_steps.append('wordpress_media_upload')
        return ApiResponse(data=payload)
    except ImageValidationError as exc:
        raise_pipeline_error(
            status_code=422,
            code='IMAGE_INVALID_OR_BLANK',
            message=exc.message,
            details=exc.details,
            retryable=False,
            step='wordpress_media_upload',
            completed_steps=completed_steps,
            failed_step='wordpress_media_upload',
        )
    except HTTPStatusError as exc:
        code = 'UPSTREAM_RATE_LIMITED' if exc.response.status_code == 429 else 'MEDIA_UPLOAD_INVALID_INPUT'
        retryable = exc.response.status_code in {429, 500, 502, 503, 504}
        raise_pipeline_error(
            status_code=exc.response.status_code,
            code=code,
            message='WordPress media upload failed.',
            details={'status_code': exc.response.status_code},
            retryable=retryable,
            step='wordpress_media_upload',
            completed_steps=completed_steps,
            failed_step='wordpress_media_upload',
            can_retry_from_step='wordpress_media_upload' if retryable else None,
        )
    except HTTPError as exc:
        raise_pipeline_error(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code='MEDIA_UPLOAD_INVALID_INPUT',
            message='WordPress media upload failed due to network timeout/connectivity issue.',
            details={'error': exc.__class__.__name__},
            retryable=True,
            step='wordpress_media_upload',
            completed_steps=completed_steps,
            failed_step='wordpress_media_upload',
            can_retry_from_step='wordpress_media_upload',
        )


@router.post('/products', response_model=ApiResponse[Any])
async def wordpress_create_product(
    body: WooProductCreateRequest,
) -> ApiResponse[Any]:
    credentials = _resolve_wordpress_credentials()
    completed_steps = ['renderform_render', 'wordpress_media_upload']

    try:
        payload = await run_with_retry(
            step='wordpress_create_product',
            operation=lambda: WordPressService.create_product(
                domain=credentials['domain'],
                wc_consumer_key=credentials['wc_consumer_key'],
                wc_consumer_secret=credentials['wc_consumer_secret'],
                payload=body,
            ),
        )
        completed_steps.append('wordpress_create_product')
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        code = 'UPSTREAM_RATE_LIMITED' if exc.response.status_code == 429 else 'WORDPRESS_CREATE_FAILED'
        retryable = exc.response.status_code in {429, 500, 502, 503, 504}
        raise_pipeline_error(
            status_code=exc.response.status_code,
            code=code,
            message='WordPress product creation failed.',
            details={'status_code': exc.response.status_code},
            retryable=retryable,
            step='wordpress_create_product',
            completed_steps=completed_steps,
            failed_step='wordpress_create_product',
            can_retry_from_step='wordpress_create_product' if retryable else None,
        )
    except HTTPError as exc:
        raise_pipeline_error(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code='WORDPRESS_CREATE_FAILED',
            message='WordPress product creation failed due to network timeout/connectivity issue.',
            details={'error': exc.__class__.__name__},
            retryable=True,
            step='wordpress_create_product',
            completed_steps=completed_steps,
            failed_step='wordpress_create_product',
            can_retry_from_step='wordpress_create_product',
        )


@router.get('/products/categories', response_model=ApiResponse[Any])
async def wordpress_list_product_categories(
    per_page: int = 100,
    page: int = 1,
) -> ApiResponse[Any]:
    credentials = _resolve_wordpress_credentials()

    try:
        payload = await run_with_retry(
            step='wordpress_list_product_categories',
            operation=lambda: WordPressService.list_product_categories(
                domain=credentials['domain'],
                wc_consumer_key=credentials['wc_consumer_key'],
                wc_consumer_secret=credentials['wc_consumer_secret'],
                per_page=per_page,
                page=page,
            ),
        )
        return ApiResponse(data=payload)
    except HTTPStatusError as exc:
        code = 'UPSTREAM_RATE_LIMITED' if exc.response.status_code == 429 else 'WORDPRESS_CREATE_FAILED'
        retryable = exc.response.status_code in {429, 500, 502, 503, 504}
        raise_pipeline_error(
            status_code=exc.response.status_code,
            code=code,
            message='WordPress product categories fetch failed.',
            details={'status_code': exc.response.status_code},
            retryable=retryable,
            step='wordpress_list_product_categories',
            completed_steps=[],
            failed_step='wordpress_list_product_categories',
            can_retry_from_step='wordpress_list_product_categories' if retryable else None,
        )
    except HTTPError as exc:
        raise_pipeline_error(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code='WORDPRESS_CATEGORIES_FAILED',
            message='WordPress product categories fetch failed due to network timeout/connectivity issue.',
            details={'error': exc.__class__.__name__},
            retryable=True,
            step='wordpress_list_product_categories',
            completed_steps=[],
            failed_step='wordpress_list_product_categories',
            can_retry_from_step='wordpress_list_product_categories',
        )

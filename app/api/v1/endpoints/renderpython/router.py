"""Free local image renderer (Pillow). Used instead of /renderform/* when USE_RENDERFORM=false.

Same request/response shapes as the RenderForm routes, so the frontend only swaps the base path.
"""
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from httpx import HTTPError

from app.core.config import get_settings
from app.schemas.common import ApiResponse, ErrorDetail
from app.schemas.renderform.render import RenderFormRenderRequest
from app.services.image import python_renderer, renderer as image_renderer
from app.services.image.python_renderer import RenderError
from app.utils.image_validation import ImageValidationError, validate_image_bytes

router = APIRouter(prefix='/renderpython', tags=['Render Python'])


def _error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=ErrorDetail(code='IMAGE_RENDER_FAILED', message=str(exc), retryable=False, step='renderpython_render').model_dump(),
    )


def _base(request: Request) -> str:
    base = str(request.base_url).rstrip('/')
    if request.headers.get('x-forwarded-proto') == 'https' and base.startswith('http://'):
        base = 'https://' + base[len('http://'):]
    return base + get_settings().API_V1_PREFIX


def _render_response(request: Request, jpeg: bytes) -> ApiResponse[Any]:
    file_id = image_renderer.store_render(jpeg)
    return ApiResponse(data={'href': f'{_base(request)}/renderpython/files/{file_id}.jpg', 'engine': 'python'})


@router.get('/health')
def renderpython_health() -> dict[str, str]:
    return {'platform': 'renderpython', 'status': 'ready'}


@router.get('/templates', response_model=ApiResponse[Any])
def renderpython_templates(request: Request) -> ApiResponse[Any]:
    """Templates from the local snapshot; preview images are served by this backend, not RenderForm's CDN."""
    base = _base(request)
    items = python_renderer.list_templates()
    for item in items:
        item['preview'] = f"{base}/renderpython/previews/{item['identifier']}.jpg"
    return ApiResponse(data=items)


@router.get('/previews/{template_id}.jpg')
def renderpython_preview(template_id: str) -> FileResponse:
    path = python_renderer.preview_path(template_id)
    if path is None:
        raise HTTPException(status_code=404, detail=ErrorDetail(code='PREVIEW_NOT_FOUND', message='Template preview not found.').model_dump())
    return FileResponse(path, media_type='image/jpeg')


@router.get('/files/{file_id}.jpg')
def renderpython_file(file_id: str) -> FileResponse:
    path = image_renderer.render_path(file_id)
    if path is None:
        raise HTTPException(status_code=404, detail=ErrorDetail(code='RENDER_NOT_FOUND', message='Rendered image not found or expired.').model_dump())
    return FileResponse(path, media_type='image/jpeg')


@router.post('/render', response_model=ApiResponse[Any])
async def renderpython_render(body: RenderFormRenderRequest, request: Request) -> ApiResponse[Any]:
    try:
        jpeg = await image_renderer.render_with_python(
            template=body.template, title=body.title_text, image_url=body.image_src.strip(), image_bytes=None
        )
    except (RenderError, ImageValidationError, HTTPError) as exc:
        raise _error(exc) from exc
    return _render_response(request, jpeg)


@router.post('/render/upload', response_model=ApiResponse[Any])
async def renderpython_render_upload(
    request: Request,
    template: str = Form(default='burly-bears-drink-shyly-1844'),
    title_text: str = Form(default='AUTOMATION TEST', alias='titleText'),
    image: UploadFile = File(...),
) -> ApiResponse[Any]:
    image_bytes = await image.read()
    try:
        validate_image_bytes(image_bytes=image_bytes, content_type=image.content_type or 'application/octet-stream')
        jpeg = await image_renderer.render_with_python(template=template, title=title_text, image_url=None, image_bytes=image_bytes)
    except (RenderError, ImageValidationError) as exc:
        raise _error(exc) from exc
    return _render_response(request, jpeg)

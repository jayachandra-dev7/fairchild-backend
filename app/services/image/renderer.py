"""Single entry point for branded product images: RenderForm API or the free local Pillow renderer.

Chosen by the USE_RENDERFORM env var (true = RenderForm, false = Python).
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.db import automation_store
from app.services.image import python_renderer
from app.services.image.python_renderer import RenderError
from app.services.platform_auth_service import platform_auth_service
from app.services.renderform.service import RenderFormService
from app.utils.image_validation import fetch_and_validate_image_url
from app.utils.retry import run_with_retry

RENDER_TTL_SECONDS = 3 * 24 * 3600
_FILE_ID = re.compile(r'^[0-9a-f]{32}$')


def use_renderform() -> bool:
    return bool(get_settings().USE_RENDERFORM)


def renders_dir() -> Path:
    path = automation_store.DB_PATH.resolve().parent / 'renders'
    path.mkdir(parents=True, exist_ok=True)
    return path


def store_render(jpeg: bytes) -> str:
    """Save a rendered JPEG for later download (used by the HTTP route); also prunes old files."""
    folder = renders_dir()
    cutoff = time.time() - RENDER_TTL_SECONDS
    for old in folder.glob('*.jpg'):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    file_id = uuid.uuid4().hex
    (folder / f'{file_id}.jpg').write_bytes(jpeg)
    return file_id


def render_path(file_id: str) -> Path | None:
    if not _FILE_ID.match(file_id):
        return None
    path = renders_dir() / f'{file_id}.jpg'
    return path if path.exists() else None


async def render_with_python(*, template: str, title: str, image_url: str | None, image_bytes: bytes | None) -> bytes:
    if image_bytes is None:
        if not image_url:
            raise RenderError('No product image supplied.')
        image_bytes, _ = await run_with_retry(step='render_fetch_image', operation=lambda: fetch_and_validate_image_url(image_url))
    return await asyncio.to_thread(python_renderer.render_template, template, title, image_bytes)


async def render_with_renderform(*, template: str, title: str, image_url: str | None, image_bytes: bytes | None, content_type: str = 'image/jpeg') -> bytes:
    api_key = platform_auth_service.get_token('renderform')
    if not api_key:
        raise RenderError('RenderForm API key is not configured (or set USE_RENDERFORM=false to use the Python renderer).')
    src = image_url or RenderFormService.to_data_url(file_bytes=image_bytes or b'', content_type=content_type)
    payload: Any = await run_with_retry(
        step='renderform_render',
        operation=lambda: RenderFormService.render_template(
            api_key=api_key, template=template, data={'title.text': title, 'image.src': src}
        ),
    )
    url = RenderFormService.extract_rendered_image_url(payload)
    if not url:
        raise RenderError('RenderForm returned no image URL.')
    rendered, _ = await run_with_retry(step='render_fetch_result', operation=lambda: fetch_and_validate_image_url(url, strict=True))
    return rendered


async def render_branded_image(
    *, template: str, title: str, image_url: str | None = None, image_bytes: bytes | None = None
) -> bytes:
    """Return the finished JPEG bytes using whichever engine USE_RENDERFORM selects."""
    if use_renderform():
        return await render_with_renderform(template=template, title=title, image_url=image_url, image_bytes=image_bytes)
    return await render_with_python(template=template, title=title, image_url=image_url, image_bytes=image_bytes)

from __future__ import annotations

from io import BytesIO
from typing import Any

import httpx
from PIL import Image, ImageStat, UnidentifiedImageError


class ImageValidationError(Exception):
    def __init__(self, code: str, message: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


BLACK_MEAN_THRESHOLD = 10.0
LOW_VARIANCE_THRESHOLD = 15.0


def validate_image_bytes(
    *,
    image_bytes: bytes,
    content_type: str,
) -> None:
    if not content_type.lower().startswith('image/'):
        raise ImageValidationError(
            code='IMAGE_INVALID_OR_BLANK',
            message='Image content type must be image/*',
            details={'content_type': content_type},
        )
    if not image_bytes:
        raise ImageValidationError(
            code='IMAGE_INVALID_OR_BLANK',
            message='Image payload is empty',
        )
    try:
        image = Image.open(BytesIO(image_bytes))
        image.verify()
        image = Image.open(BytesIO(image_bytes))
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageValidationError(
            code='IMAGE_INVALID_OR_BLANK',
            message='Image is corrupted or unreadable',
        ) from exc

    grayscale = image.convert('L')
    stat = ImageStat.Stat(grayscale)
    mean = stat.mean[0]
    variance = stat.var[0]
    if mean < BLACK_MEAN_THRESHOLD or variance < LOW_VARIANCE_THRESHOLD:
        raise ImageValidationError(
            code='IMAGE_INVALID_OR_BLANK',
            message='Image is blank/near-black',
            details={'mean': round(mean, 3), 'variance': round(variance, 3)},
        )


async def fetch_and_validate_image_url(
    image_url: str,
    *,
    timeout: float = 20.0,
) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        head_resp = await client.head(image_url)
        content_type = head_resp.headers.get('content-type', '')
        content_len = head_resp.headers.get('content-length')
        if content_len is not None and content_len.strip() == '0':
            raise ImageValidationError(
                code='IMAGE_INVALID_OR_BLANK',
                message='Image URL points to zero-length payload',
            )

        get_resp = await client.get(image_url)
        get_resp.raise_for_status()
        resolved_type = get_resp.headers.get('content-type', content_type or 'application/octet-stream')
        validate_image_bytes(image_bytes=get_resp.content, content_type=resolved_type)
        return get_resp.content, resolved_type

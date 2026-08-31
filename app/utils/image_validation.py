from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

import httpx
from PIL import Image, ImageStat, UnidentifiedImageError

logger = logging.getLogger(__name__)

# Browser UA: some affiliate CDNs 403 default httpx/library agents.
BROWSER_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'


class ImageValidationError(Exception):
    def __init__(self, code: str, message: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


BLACK_MEAN_THRESHOLD = 10.0
LOW_VARIANCE_THRESHOLD = 15.0

# Above this share, one color dominates the whole canvas. A legitimate product photo
# — even shot on white — has enough product/shadow/edge pixels to break a 92%+ single-color
# majority; a template that dropped the photo layer renders text on an untouched background,
# which does not. Unlike a plain brightness/variance check, this catches ANY blank background
# color (white, black, brand gray, ...) with one threshold.
DOMINANT_COLOR_MAX_SHARE = 0.92
BLANK_CANVAS_SAMPLE_SIZE = (64, 64)


def _dominant_color_share(image: Image.Image) -> float:
    sample = image.convert('RGB').resize(BLANK_CANVAS_SAMPLE_SIZE)
    colors = sample.getcolors(maxcolors=BLANK_CANVAS_SAMPLE_SIZE[0] * BLANK_CANVAS_SAMPLE_SIZE[1])
    if not colors:
        return 0.0
    total_pixels = BLANK_CANVAS_SAMPLE_SIZE[0] * BLANK_CANVAS_SAMPLE_SIZE[1]
    dominant_count = max(count for count, _ in colors)
    return dominant_count / total_pixels


def validate_image_bytes(
    *,
    image_bytes: bytes,
    content_type: str,
    strict: bool = False,
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

    # Brightness/variance is a soft signal only. White-background product shots (and other
    # legitimately low-variance images) are valid, so a dark/low-variance decode is logged
    # and tolerated rather than raised. Only genuinely undecodable bytes fail above.
    grayscale = image.convert('L')
    stat = ImageStat.Stat(grayscale)
    mean = stat.mean[0]
    variance = stat.var[0]
    if mean < BLACK_MEAN_THRESHOLD or variance < LOW_VARIANCE_THRESHOLD:
        logger.warning(
            'Image brightness/variance below thresholds but accepted (mean=%.3f, variance=%.3f)',
            mean,
            variance,
        )

    dominant_share = _dominant_color_share(image)
    if dominant_share > DOMINANT_COLOR_MAX_SHARE:
        if strict:
            raise ImageValidationError(
                code='IMAGE_INVALID_OR_BLANK',
                message='Rendered image has no visible product photo (background is one uniform color).',
                details={'dominantColorShare': round(dominant_share, 4)},
            )
        logger.warning(
            'Image dominant color share above threshold but accepted in non-strict mode (share=%.3f)',
            dominant_share,
        )


async def fetch_and_validate_image_url(
    image_url: str,
    *,
    timeout: float = 20.0,
    strict: bool = False,
) -> tuple[bytes, str]:
    headers = {'User-Agent': BROWSER_USER_AGENT}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
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
        validate_image_bytes(image_bytes=get_resp.content, content_type=resolved_type, strict=strict)
        return get_resp.content, resolved_type

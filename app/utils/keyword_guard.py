from __future__ import annotations

import re

from app.utils.pipeline_errors import raise_pipeline_error

URL_LIKE_PATTERN = re.compile(r'^(https?://|www\.)', re.IGNORECASE)


def normalize_keyword_for_search(keyword: str, *, step: str) -> str:
    normalized = ' '.join(keyword.strip().split())
    if not normalized:
        raise_pipeline_error(
            status_code=422,
            code='INVALID_KEYWORD_FORMAT',
            message='Enter related keyword (2-3 words). URLs are not supported for keyword search.',
            retryable=False,
            step=step,
        )
    if URL_LIKE_PATTERN.match(normalized):
        raise_pipeline_error(
            status_code=422,
            code='INVALID_KEYWORD_FORMAT',
            message='Enter related keyword (2-3 words). URLs are not supported for keyword search.',
            retryable=False,
            step=step,
        )
    return normalized

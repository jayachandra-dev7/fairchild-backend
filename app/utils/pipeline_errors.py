from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.schemas.common import ErrorDetail


def raise_pipeline_error(
    *,
    status_code: int,
    code: str,
    message: str,
    step: str,
    retryable: bool,
    details: Any | None = None,
    completed_steps: list[str] | None = None,
    failed_step: str | None = None,
    can_retry_from_step: str | None = None,
) -> None:
    payload_details = details
    progress = {}
    if completed_steps is not None:
        progress['completedSteps'] = completed_steps
    if failed_step is not None:
        progress['failedStep'] = failed_step
    if can_retry_from_step is not None:
        progress['canRetryFromStep'] = can_retry_from_step
    if progress:
        payload_details = {'diagnostic': details, 'progress': progress}

    error = ErrorDetail(
        code=code,
        message=message,
        details=payload_details,
        retryable=retryable,
        step=step,
    )
    raise HTTPException(status_code=status_code, detail=error.model_dump(exclude_none=True))

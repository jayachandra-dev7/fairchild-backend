from typing import Any

from fastapi import APIRouter, HTTPException, status
from httpx import HTTPError, HTTPStatusError

from app.core.config import get_settings
from app.schemas.claude.generate import ClaudeGenerateRequest, ClaudeGenerateResponse
from app.schemas.common import ApiResponse, ErrorDetail
from app.services.claude.service import ClaudeService

router = APIRouter(prefix='/claude', tags=['Claude'])
settings = get_settings()


@router.get('/health')
def claude_health() -> dict[str, str]:
    return {'platform': 'claude', 'status': 'ready'}


@router.post('/generate', response_model=ApiResponse[ClaudeGenerateResponse])
async def claude_generate(body: ClaudeGenerateRequest) -> ApiResponse[ClaudeGenerateResponse]:
    if not settings.CLAUDE_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorDetail(
                code='MISSING_AUTHORIZATION',
                message='Set CLAUDE_API_KEY in .env',
            ).model_dump(),
        )

    try:
        payload = await ClaudeService.generate_with_fallback(
            api_key=settings.CLAUDE_API_KEY,
            model_candidates=body.model_candidates,
            prompt=body.prompt,
            max_tokens=body.max_tokens,
            temperature=body.temperature,
        )
        return ApiResponse(
            data=ClaudeGenerateResponse(
                model=payload['model'],
                text=payload['text'],
            )
        )
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=ErrorDetail(
                code='CLAUDE_API_ERROR',
                message=f'Claude API error: {exc.response.status_code}',
                details=exc.response.text,
            ).model_dump(),
        ) from exc
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code='CLAUDE_CONNECTIVITY_ERROR',
                message='Unable to reach Claude API',
            ).model_dump(),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(
                code='CLAUDE_MODEL_NOT_FOUND',
                message=str(exc),
            ).model_dump(),
        ) from exc

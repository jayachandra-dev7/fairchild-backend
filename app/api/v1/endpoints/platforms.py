from fastapi import APIRouter

from app.schemas.common import ApiResponse

router = APIRouter(prefix='/platforms', tags=['Platforms'])


@router.get('', response_model=ApiResponse[dict[str, list[str]]])
def list_platforms() -> ApiResponse[dict[str, list[str]]]:
    return ApiResponse(data={'platforms': ['cj', 'impact', 'wordpress', 'metricool']})

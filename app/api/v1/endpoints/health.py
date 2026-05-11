from fastapi import APIRouter

from app.schemas.common import ApiResponse
from app.services.health_service import HealthService

router = APIRouter(prefix='/health', tags=['Health'])


@router.get('', response_model=ApiResponse[dict[str, str]])
def get_health() -> ApiResponse[dict[str, str]]:
    return ApiResponse(data=HealthService.get_status())

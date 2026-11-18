from fastapi import APIRouter

from app.schemas.common import ApiResponse
from app.services.health_service import HealthService

router = APIRouter(prefix='/health', tags=['Health'])


@router.get('', response_model=ApiResponse[dict[str, str]])
def get_health() -> ApiResponse[dict[str, str]]:
    return ApiResponse(data=HealthService.get_status())


@router.get('/render-engine', response_model=ApiResponse[dict[str, str]], include_in_schema=True)
def get_render_engine() -> ApiResponse[dict[str, str]]:
    """Tells the frontend which image engine USE_RENDERFORM selects, and the API base path to call."""
    from app.core.config import get_settings

    use_rf = bool(get_settings().USE_RENDERFORM)
    return ApiResponse(data={'engine': 'renderform' if use_rf else 'python', 'basePath': '/renderform' if use_rf else '/renderpython'})

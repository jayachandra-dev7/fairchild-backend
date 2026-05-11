from fastapi import APIRouter

from app.schemas.common import ApiResponse
from app.schemas.platform_auth import PlatformAuthorizeRequest, PlatformAuthorizeResponse
from app.services.platform_auth_service import platform_auth_service

router = APIRouter(prefix='/wordpress', tags=['WordPress'])


@router.get('/health')
def wordpress_health() -> dict[str, str]:
    return {'platform': 'wordpress', 'status': 'ready'}


@router.post('/authorize', response_model=ApiResponse[PlatformAuthorizeResponse])
def wordpress_authorize(body: PlatformAuthorizeRequest) -> ApiResponse[PlatformAuthorizeResponse]:
    platform_auth_service.set_token('wordpress', body.token)
    return ApiResponse(
        data=PlatformAuthorizeResponse(
            platform='wordpress',
            authorized=True,
            message='WordPress token saved successfully',
        )
    )

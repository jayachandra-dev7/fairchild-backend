from fastapi import APIRouter

from app.schemas.common import ApiResponse
from app.schemas.platform_auth import PlatformAuthorizeRequest, PlatformAuthorizeResponse
from app.services.platform_auth_service import platform_auth_service

router = APIRouter(prefix='/impact', tags=['Impact'])


@router.get('/health')
def impact_health() -> dict[str, str]:
    return {'platform': 'impact', 'status': 'ready'}


@router.post('/authorize', response_model=ApiResponse[PlatformAuthorizeResponse])
def impact_authorize(body: PlatformAuthorizeRequest) -> ApiResponse[PlatformAuthorizeResponse]:
    platform_auth_service.set_token('impact', body.token)
    return ApiResponse(
        data=PlatformAuthorizeResponse(
            platform='impact',
            authorized=True,
            message='Impact token saved successfully',
        )
    )

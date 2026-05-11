from fastapi import APIRouter

from app.schemas.common import ApiResponse
from app.schemas.platform_auth import PlatformAuthorizeRequest, PlatformAuthorizeResponse
from app.services.platform_auth_service import platform_auth_service

router = APIRouter(prefix='/metricool', tags=['Metricool'])


@router.get('/health')
def metricool_health() -> dict[str, str]:
    return {'platform': 'metricool', 'status': 'ready'}


@router.post('/authorize', response_model=ApiResponse[PlatformAuthorizeResponse])
def metricool_authorize(body: PlatformAuthorizeRequest) -> ApiResponse[PlatformAuthorizeResponse]:
    platform_auth_service.set_token('metricool', body.token)
    return ApiResponse(
        data=PlatformAuthorizeResponse(
            platform='metricool',
            authorized=True,
            message='Metricool token saved successfully',
        )
    )

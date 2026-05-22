from fastapi import FastAPI
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.schemas.common import ApiResponse, ErrorDetail
from app.services.platform_auth_service import platform_auth_service

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url='/docs',
    redoc_url='/redoc',
)


@app.get('/')
def root() -> dict[str, str]:
    return {'status': 'ok', 'service': settings.APP_NAME, 'version': settings.APP_VERSION}

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_LOCAL_ORIGIN, settings.FRONTEND_PROD_ORIGIN],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.exception_handler(FastAPIHTTPException)
async def http_exception_handler(_, exc: FastAPIHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {'code': 'HTTP_ERROR', 'message': str(exc.detail)}
    error = ErrorDetail(**detail)
    body = ApiResponse(success=False, error=error).model_dump(exclude_none=True)
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_, exc: RequestValidationError) -> JSONResponse:
    error = ErrorDetail(
        code='VALIDATION_ERROR',
        message='Request validation failed.',
        details=exc.errors(),
        retryable=False,
    )
    body = ApiResponse(success=False, error=error).model_dump(exclude_none=True)
    return JSONResponse(status_code=422, content=body)


@app.exception_handler(Exception)
async def generic_exception_handler(_, __: Exception) -> JSONResponse:
    error = ErrorDetail(
        code='INTERNAL_SERVER_ERROR',
        message='Unexpected server error.',
        retryable=False,
    )
    body = ApiResponse(success=False, error=error).model_dump(exclude_none=True)
    return JSONResponse(status_code=500, content=body)


@app.on_event('startup')
async def preload_platform_credentials() -> None:
    if settings.CJ_TOKEN:
        platform_auth_service.set_token('cj', settings.CJ_TOKEN)

    if settings.METRICOOL_TOKEN:
        platform_auth_service.set_token('metricool', settings.METRICOOL_TOKEN)

    if settings.RENDERFORM_API_KEY:
        platform_auth_service.set_token('renderform', settings.RENDERFORM_API_KEY)

    if settings.IMPACT_ACCOUNT_SID and settings.IMPACT_AUTH_TOKEN:
        platform_auth_service.set_credentials(
            'impact',
            {
                'account_sid': settings.IMPACT_ACCOUNT_SID,
                'auth_token': settings.IMPACT_AUTH_TOKEN,
            },
        )

    if settings.WORDPRESS_DOMAIN and settings.WORDPRESS_WC_CONSUMER_KEY and settings.WORDPRESS_WC_CONSUMER_SECRET:
        platform_auth_service.set_credentials(
            'wordpress',
            {
                'domain': settings.WORDPRESS_DOMAIN,
                'wc_consumer_key': settings.WORDPRESS_WC_CONSUMER_KEY,
                'wc_consumer_secret': settings.WORDPRESS_WC_CONSUMER_SECRET,
            },
        )


app.include_router(api_router, prefix=settings.API_V1_PREFIX)

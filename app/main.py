import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_cors_origins, get_settings
from app.schemas.common import ApiResponse, ErrorDetail
from app.services.platform_auth_service import platform_auth_service
from app.workers import scheduler

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await preload_platform_credentials()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url='/docs',
    redoc_url='/redoc',
    lifespan=lifespan,
)


@app.get('/')
def root() -> dict[str, str]:
    return {'status': 'ok', 'service': settings.APP_NAME, 'version': settings.APP_VERSION}

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(settings),
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
    # Pydantic v2 puts the raw exception object in each error's `ctx` when a custom
    # validator raises. `default=str` keeps those (and any other exotic value) encodable
    # so validation failures stay 422 instead of falling through to the 500 handler.
    details = json.loads(json.dumps(exc.errors(), default=str))
    error = ErrorDetail(
        code='VALIDATION_ERROR',
        message='Request validation failed.',
        details=details,
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

    # The credential field names are historical: the pair is sent as HTTP Basic auth, so a WordPress
    # username + Application Password can be stored in the same slots and takes precedence.
    wp_user = settings.WORDPRESS_USERNAME.strip() or settings.WORDPRESS_WC_CONSUMER_KEY
    wp_secret = settings.WORDPRESS_APP_PASSWORD.strip() if settings.WORDPRESS_USERNAME.strip() else settings.WORDPRESS_WC_CONSUMER_SECRET
    if settings.WORDPRESS_DOMAIN and wp_user and wp_secret:
        platform_auth_service.set_credentials(
            'wordpress',
            {
                'domain': settings.WORDPRESS_DOMAIN,
                'wc_consumer_key': wp_user,
                'wc_consumer_secret': wp_secret,
            },
        )


app.include_router(api_router, prefix=settings.API_V1_PREFIX)

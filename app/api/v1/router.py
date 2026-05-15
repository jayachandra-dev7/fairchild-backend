from fastapi import APIRouter

from app.api.v1.endpoints import health, platforms
from app.api.v1.endpoints.claude import router as claude_router
from app.api.v1.endpoints.cj import router as cj_router
from app.api.v1.endpoints.impact import router as impact_router
from app.api.v1.endpoints.metricool import router as metricool_router
from app.api.v1.endpoints.renderform import router as renderform_router
from app.api.v1.endpoints.wordpress import router as wordpress_router

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(platforms.router)
api_router.include_router(claude_router.router)
api_router.include_router(cj_router.router)
api_router.include_router(impact_router.router)
api_router.include_router(wordpress_router.router)
api_router.include_router(metricool_router.router)
api_router.include_router(renderform_router.router)

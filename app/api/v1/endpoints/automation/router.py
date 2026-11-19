from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.core.config import get_settings
from app.db import automation_store
from app.schemas.automation import AutomationSettings, RunRequest
from app.schemas.common import ApiResponse, ErrorDetail
from app.services.automation import blog_job, categories, config_store, sources
from app.workers import runner


router = APIRouter(prefix='/automation', tags=['GPC Automation'])


def _err(code: str, message: str, http: int = 400) -> HTTPException:
    return HTTPException(status_code=http, detail=ErrorDetail(code=code, message=message).model_dump())


@router.get('/settings', response_model=ApiResponse[AutomationSettings])
def get_automation_settings() -> ApiResponse[AutomationSettings]:
    return ApiResponse(data=config_store.load_all())


@router.put('/settings', response_model=ApiResponse[AutomationSettings])
def put_automation_settings(body: AutomationSettings) -> ApiResponse[AutomationSettings]:
    return ApiResponse(data=config_store.save_all(body))


@router.get('/status', response_model=ApiResponse[dict])
def automation_status() -> ApiResponse[dict]:
    snapshot_info: dict[str, Any] = {}
    try:
        snap = categories.load_snapshot()
        snapshot_info = {'synced_at': snap['synced_at'], 'total': snap['total'], 'leaf_count': snap['leaf_count']}
    except FileNotFoundError:
        snapshot_info = {'missing': True}
    cursor = int(automation_store.get_state('product_cursor', '0') or 0)
    return ApiResponse(
        data={
            'product': runner.next_run_info('product'),
            'blog': runner.next_run_info('blog'),
            'categories': snapshot_info,
            'product_cursor': cursor,
            'scheduler_enabled': True,
        }
    )


@router.get('/sources', response_model=ApiResponse[dict])
async def automation_sources(refresh: bool = Query(default=False)) -> ApiResponse[dict]:
    """Live CJ advertisers and active Impact campaigns for the UI pickers (same APIs as the affiliate poster)."""
    cj, impact = await asyncio.gather(
        sources.list_cj_advertisers(force=refresh), sources.list_impact_campaigns(force=refresh), return_exceptions=True
    )
    return ApiResponse(
        data={
            'cj': cj if not isinstance(cj, BaseException) else [],
            'cj_error': str(cj) if isinstance(cj, BaseException) else None,
            'impact': impact if not isinstance(impact, BaseException) else [],
            'impact_error': str(impact) if isinstance(impact, BaseException) else None,
        }
    )


def _start(kind: str, body: RunRequest) -> ApiResponse[dict]:
    try:
        run_id = runner.start_run(
            kind, trigger='manual', dry_run=body.dry_run, max_categories=body.max_categories, category_ids=body.category_ids
        )
    except runner.RunAlreadyActive as exc:
        raise _err('RUN_ALREADY_ACTIVE', str(exc), 409) from exc
    return ApiResponse(data={'run_id': run_id, 'dry_run': body.dry_run})


@router.post('/product/run', response_model=ApiResponse[dict])
async def run_product(body: RunRequest) -> ApiResponse[dict]:
    try:
        categories.load_snapshot()
    except FileNotFoundError as exc:
        raise _err('CATEGORY_SNAPSHOT_MISSING', str(exc), 409) from exc
    return _start('product', body)


@router.post('/blog/run', response_model=ApiResponse[dict])
async def run_blog(body: RunRequest) -> ApiResponse[dict]:
    return _start('blog', body)


class CustomBlogPrompt(BaseModel):
    prompt: str = Field(min_length=5, max_length=4000)


class CustomBlogPublish(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    excerpt: str = ''
    status: str = Field(default='draft', pattern='^(publish|draft|pending)$')


def _upstream(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f'{exc.response.status_code} {exc.response.text[:200]}'
    return f'{exc.__class__.__name__}: {str(exc)[:200]}'


@router.post('/blog/custom/generate', response_model=ApiResponse[dict])
async def custom_blog_generate(body: CustomBlogPrompt) -> ApiResponse[dict]:
    """Draft a blog post from the editor's prompt. Not published; the UI shows it for review."""
    try:
        return ApiResponse(data=await blog_job.generate_custom_blog(body.prompt))
    except Exception as exc:  # noqa: BLE001
        raise _err('CUSTOM_BLOG_GENERATE_FAILED', _upstream(exc), 502) from exc


@router.post('/blog/custom/publish', response_model=ApiResponse[dict])
async def custom_blog_publish(body: CustomBlogPublish) -> ApiResponse[dict]:
    try:
        return ApiResponse(data=await blog_job.publish_custom_blog(title=body.title, content=body.content, excerpt=body.excerpt, status=body.status))
    except Exception as exc:  # noqa: BLE001
        raise _err('CUSTOM_BLOG_PUBLISH_FAILED', _upstream(exc), 502) from exc


@router.get('/runs', response_model=ApiResponse[list])
def list_runs(kind: str | None = Query(default=None, pattern='^(product|blog)$'), limit: int = Query(default=20, ge=1, le=100)) -> ApiResponse[list]:
    return ApiResponse(data=automation_store.list_runs(kind, limit))


@router.post('/runs/{run_id}/cancel', response_model=ApiResponse[dict])
def cancel_run(run_id: int) -> ApiResponse[dict]:
    if not automation_store.get_run(run_id):
        raise _err('RUN_NOT_FOUND', f'Run {run_id} not found.', 404)
    if not runner.cancel_run(run_id):
        raise _err('RUN_NOT_RUNNING', f'Run {run_id} is not running.', 409)
    return ApiResponse(data={'run_id': run_id, 'cancelling': True})


@router.get('/runs/{run_id}', response_model=ApiResponse[dict])
def get_run(run_id: int) -> ApiResponse[dict]:
    run = automation_store.get_run(run_id)
    if not run:
        raise _err('RUN_NOT_FOUND', f'Run {run_id} not found.', 404)
    return ApiResponse(data={**run, 'items': automation_store.list_run_items(run_id)})


@router.get('/categories', response_model=ApiResponse[dict])
def preview_categories(limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0)) -> ApiResponse[dict]:
    try:
        leaves = sorted(categories.leaf_categories(), key=lambda c: c['id'])
    except FileNotFoundError as exc:
        raise _err('CATEGORY_SNAPSHOT_MISSING', str(exc), 409) from exc
    return ApiResponse(data={'total_leaves': len(leaves), 'items': leaves[offset : offset + limit]})


@router.post('/categories/sync', response_model=ApiResponse[dict])
async def sync_categories() -> ApiResponse[dict]:
    try:
        snap = await categories.sync_snapshot()
    except Exception as exc:  # noqa: BLE001
        raise _err('CATEGORY_SYNC_FAILED', f'{exc.__class__.__name__}: {str(exc)[:200]}', 502) from exc
    return ApiResponse(data={'synced_at': snap['synced_at'], 'total': snap['total'], 'leaf_count': snap['leaf_count']})

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel, Field
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile, status

from app.core.config import get_settings
from app.db import automation_store
from app.schemas.automation import AutomationSettings, RunRequest
from app.schemas.common import ApiResponse, ErrorDetail
from app.services.automation import blog_job, calendar_job, categories, config_store, sources
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


# ---------------------------------------------------------------- content calendar
class CalendarRow(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    keyword: str = Field(default='', max_length=120)
    publish_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')


class CalendarImport(BaseModel):
    batch: str = Field(default='Plan', min_length=1, max_length=80)
    guidelines: str = Field(default='', max_length=1000)
    articles: list[CalendarRow] = Field(min_length=1, max_length=500)


class CalendarEdit(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    keyword: str | None = Field(default=None, max_length=120)
    publish_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    publish_time: str | None = Field(default=None, pattern=r'^(([01]\d|2[0-3]):[0-5]\d)?$')
    excerpt: str | None = None
    content: str | None = None
    state: str | None = Field(default=None, pattern='^(generated|approved)$')


class CalendarIds(BaseModel):
    ids: list[int] | None = None  # None = every eligible row


class CalendarSchedule(CalendarIds):
    time: str = Field(default='09:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    timezone: str = Field(default='America/New_York', max_length=64)


def _cal_out(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in row.items() if k != 'content'}
    out['has_content'] = bool(row['content'])
    out['needs_fact_check'] = calendar_job.needs_fact_check(row)
    return out


@router.post('/calendar/parse', response_model=ApiResponse[dict])
async def calendar_parse(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    start_date: str | None = Form(default=None),
    per_week: int = Form(default=5),
    instructions: str = Form(default=''),
) -> ApiResponse[dict]:
    """Read an uploaded plan (PDF/DOCX/CSV/text) or pasted text into article rows. Nothing is saved yet."""
    try:
        start = date.fromisoformat(start_date) if start_date else None
        data = await file.read() if file else None
        result = await calendar_job.parse_plan(filename=file.filename if file else '', data=data, text=text, start=start, per_week=per_week, instructions=instructions)
    except ValueError as exc:
        raise _err('CALENDAR_PARSE_FAILED', str(exc), 422) from exc
    except Exception as exc:  # noqa: BLE001
        raise _err('CALENDAR_PARSE_FAILED', _upstream(exc), 502) from exc
    return ApiResponse(data=result)


@router.post('/calendar/import', response_model=ApiResponse[dict])
def calendar_import(body: CalendarImport) -> ApiResponse[dict]:
    added, skipped = automation_store.calendar_add_many(body.batch, [r.model_dump() for r in body.articles])
    if body.guidelines.strip():
        automation_store.set_state(f'calendar_guidelines:{body.batch}', body.guidelines.strip())
    return ApiResponse(data={'added': added, 'skipped': skipped})


@router.get('/calendar', response_model=ApiResponse[dict])
def calendar_list() -> ApiResponse[dict]:
    if not calendar_job.is_generating():
        automation_store.calendar_reset_stuck()
    rows = automation_store.calendar_list()
    return ApiResponse(data={'items': [_cal_out(r) for r in rows], 'generating': calendar_job.is_generating()})


@router.delete('/calendar', response_model=ApiResponse[dict])
def calendar_delete_plan(batch: str = Query(min_length=1, max_length=80)) -> ApiResponse[dict]:
    """Delete a whole plan (batch). Articles already scheduled or published stay in WordPress."""
    if calendar_job.is_generating():
        raise _err('CALENDAR_BUSY', 'Drafts are being written. Wait for them to finish before deleting a plan.', 409)
    return ApiResponse(data={'deleted': automation_store.calendar_delete_batch(batch)})


@router.get('/calendar/{post_id}', response_model=ApiResponse[dict])
def calendar_get(post_id: int) -> ApiResponse[dict]:
    row = automation_store.calendar_get(post_id)
    if not row:
        raise _err('CALENDAR_NOT_FOUND', 'Article not found.', 404)
    return ApiResponse(data={**_cal_out(row), 'content': row['content']})


@router.patch('/calendar/{post_id}', response_model=ApiResponse[dict])
def calendar_edit(post_id: int, body: CalendarEdit) -> ApiResponse[dict]:
    row = automation_store.calendar_get(post_id)
    if not row:
        raise _err('CALENDAR_NOT_FOUND', 'Article not found.', 404)
    fields = body.model_dump(exclude_none=True)
    if fields.get('state') == 'approved' and not (fields.get('content') or row['content']):
        raise _err('CALENDAR_NO_DRAFT', 'Generate the draft before approving.', 409)
    if fields.get('state') and row['state'] in ('planned', 'generating', 'error') and not row['content']:
        fields.pop('state')
    # Editing an article that was already scheduled puts it back to review; re-scheduling updates the same WordPress post.
    if row['state'] == 'scheduled' and any(k in fields for k in ('title', 'publish_date', 'publish_time', 'excerpt', 'content')) and 'state' not in fields:
        fields['state'] = 'approved'
    return ApiResponse(data=_cal_out(automation_store.calendar_update(post_id, **fields) or row))


@router.delete('/calendar/{post_id}', response_model=ApiResponse[dict])
def calendar_remove(post_id: int) -> ApiResponse[dict]:
    row = automation_store.calendar_get(post_id)
    if row and row['state'] == 'scheduled':
        raise _err('CALENDAR_SCHEDULED', 'This article is already scheduled in WordPress. Remove it there first.', 409)
    automation_store.calendar_delete(post_id)
    return ApiResponse(data={'deleted': post_id})


@router.post('/calendar/generate', response_model=ApiResponse[dict])
async def calendar_generate(body: CalendarIds) -> ApiResponse[dict]:
    ids = body.ids if body.ids is not None else [r['id'] for r in automation_store.calendar_list() if r['state'] in ('planned', 'error')]
    try:
        queued = calendar_job.start_generation(ids)
    except RuntimeError as exc:
        raise _err('CALENDAR_BUSY', str(exc), 409) from exc
    return ApiResponse(data={'queued': queued})


@router.post('/calendar/approve', response_model=ApiResponse[dict])
def calendar_approve(body: CalendarIds) -> ApiResponse[dict]:
    rows = automation_store.calendar_list()
    ids = set(body.ids) if body.ids is not None else {r['id'] for r in rows}
    n = 0
    for r in rows:
        if r['id'] in ids and r['state'] == 'generated':
            automation_store.calendar_update(r['id'], state='approved')
            n += 1
    return ApiResponse(data={'approved': n})


@router.post('/calendar/{post_id}/publish-now', response_model=ApiResponse[dict])
async def calendar_publish_now(post_id: int) -> ApiResponse[dict]:
    """Publish this one article immediately, regardless of its planned date."""
    try:
        row = await calendar_job.schedule_one(post_id, '00:00', 'UTC', now=True)
    except ValueError as exc:
        raise _err('CALENDAR_PUBLISH_FAILED', str(exc), 422) from exc
    except Exception as exc:  # noqa: BLE001
        raise _err('CALENDAR_PUBLISH_FAILED', _upstream(exc), 502) from exc
    return ApiResponse(data=_cal_out(row))


@router.post('/calendar/schedule', response_model=ApiResponse[dict])
async def calendar_schedule(body: CalendarSchedule) -> ApiResponse[dict]:
    """Send approved articles to WordPress as scheduled posts (WordPress publishes them on their dates)."""
    rows = automation_store.calendar_list()
    wanted = set(body.ids) if body.ids is not None else None
    targets = [r for r in rows if r['state'] == 'approved' and (wanted is None or r['id'] in wanted)]
    done, immediate, failed = 0, 0, []
    for r in targets:
        try:
            out = await calendar_job.schedule_one(r['id'], body.time, body.timezone)
            done += 1
            immediate += 1 if out.get('scheduled_for') == 'now' else 0
        except ValueError as exc:
            raise _err('CALENDAR_SCHEDULE_FAILED', str(exc), 422) from exc
        except Exception as exc:  # noqa: BLE001
            automation_store.calendar_update(r['id'], error=_upstream(exc))
            failed.append({'id': r['id'], 'title': r['title'], 'error': _upstream(exc)})
    return ApiResponse(data={'scheduled': done, 'published_now': immediate, 'failed': failed})


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

"""Starts automation runs as background tasks and records their outcome."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.db import automation_store
from app.services.automation import blog_job, config_store, guide_job, product_job

logger = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()
_running: set[str] = set()
_run_tasks: dict[int, asyncio.Task] = {}


class RunAlreadyActive(RuntimeError):
    pass


def _settings_for(kind: str, cfg: Any) -> Any:
    return cfg.product if kind == 'product' else config_store.load_guide() if kind == 'guide' else cfg.blog


def local_now(tz_name: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001 - bad tz name falls back to UTC rather than breaking the scheduler
        return datetime.now(ZoneInfo('UTC'))


async def _execute(run_id: int, kind: str, dry_run: bool, max_categories: int | None, category_ids: list[int] | None) -> None:
    try:
        cfg = config_store.load_all()
        if kind == 'product':
            status, summary = await product_job.run_product_job(
                run_id, cfg.product, dry_run=dry_run, max_categories=max_categories, category_ids=category_ids
            )
        elif kind == 'guide':
            status, summary = await guide_job.run_guide_job(run_id, config_store.load_guide(), dry_run=dry_run)
        else:
            status, summary = await blog_job.run_blog_job(run_id, cfg.blog, dry_run=dry_run)
        automation_store.finish_run(run_id, status, summary)
    except asyncio.CancelledError:
        # Stopped by the user (Stop button). Work already done stays; nothing more is started.
        automation_store.add_run_item(run_id, status='warn', message='Run stopped by user.')
        run = automation_store.get_run(run_id) or {}
        automation_store.finish_run(run_id, 'cancelled', run.get('summary') or {})
    except Exception as exc:  # noqa: BLE001
        logger.exception('automation_run_crashed')
        automation_store.add_run_item(run_id, status='error', message=f'Run crashed: {exc.__class__.__name__}: {str(exc)[:300]}')
        run = automation_store.get_run(run_id) or {}
        automation_store.finish_run(run_id, 'failed', {**(run.get('summary') or {}), 'error': str(exc)[:300]})
    finally:
        _running.discard(kind)
        _run_tasks.pop(run_id, None)


def start_run(
    kind: str,
    *,
    trigger: str,
    dry_run: bool = False,
    max_categories: int | None = None,
    category_ids: list[int] | None = None,
) -> int:
    if kind not in ('product', 'blog', 'guide'):
        raise ValueError('kind must be product, blog or guide')
    if kind in _running:
        raise RunAlreadyActive(f'A {kind} run is already in progress.')
    loop = asyncio.get_running_loop()  # raises if called off the event loop - before any state is touched
    cfg = config_store.load_all()
    tz = _settings_for(kind, cfg).timezone
    run_id = automation_store.try_create_run(kind=kind, trigger=trigger, dry_run=dry_run, local_date=local_now(tz).date().isoformat())
    if run_id is None:
        raise RunAlreadyActive(f'A {kind} run is already in progress.')
    _running.add(kind)
    try:
        task = loop.create_task(_execute(run_id, kind, dry_run, max_categories, category_ids))
    except Exception:
        _running.discard(kind)
        automation_store.finish_run(run_id, 'failed', {'error': 'could not start'})
        raise
    _tasks.add(task)
    _run_tasks[run_id] = task
    task.add_done_callback(_tasks.discard)
    return run_id


def cancel_run(run_id: int) -> bool:
    """Stop a running run. Returns False when the run is not running."""
    task = _run_tasks.get(run_id)
    if task is not None and not task.done():
        task.cancel()
        return True
    run = automation_store.get_run(run_id)
    if run and run['status'] == 'running':  # orphaned row (no live task): close it out
        # no live task = the server restarted / lost it, so this is an interrupted run (a scheduled one gets retried)
        automation_store.finish_run(run_id, 'interrupted', run.get('summary') or {})
        _running.discard(run['kind'])
        return True
    return False


def next_run_info(kind: str) -> dict[str, Any]:
    from datetime import timedelta

    from app.workers import scheduler

    cfg = config_store.load_all()
    s = _settings_for(kind, cfg)
    now = local_now(s.timezone)
    attempts = scheduler.slot_attempts(kind, s, now)
    done = scheduler.is_done(attempts)
    retrying = bool(attempts) and not done
    nxt = None if done else scheduler.next_attempt_at(s, now, attempts)
    if nxt is None:  # finished today, or the retry window ran out: next try is tomorrow's slot
        nxt = scheduler._slot(s, now) + timedelta(days=1)
        retrying = False
    return {'enabled': s.enabled, 'next_run_local': nxt.isoformat(timespec='minutes'), 'timezone': s.timezone, 'ran_today': done, 'retrying': retrying}

"""One-minute tick that starts the daily product / blog runs when they are due."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import get_settings
from app.db import automation_store
from app.schemas.automation import ScheduleSettings
from app.services.automation import config_store
from app.workers import runner

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None


RETRY_EVERY = timedelta(minutes=5)
RETRY_WINDOW = timedelta(hours=2)  # after the scheduled time, a failed attempt is retried every 5 minutes for this long
FINISHED = {'running', 'completed', 'partial', 'cancelled'}  # a failed / interrupted attempt does NOT count as done


def _slot(cfg: ScheduleSettings, now_local: datetime) -> datetime:
    hh, mm = (int(x) for x in cfg.daily_time.split(':'))
    return now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)


def slot_attempts(kind: str, cfg: ScheduleSettings, now_local: datetime) -> list[dict]:
    """Scheduled runs started since today's scheduled time. Anything earlier (an old, cancelled or failed run, or a
    run from before the time was changed) never blocks the schedule."""
    since = _slot(cfg, now_local).astimezone(timezone.utc).isoformat(timespec='seconds')
    return automation_store.scheduled_attempts_since(kind, since)


def is_done(attempts: list[dict]) -> bool:
    return any(a['status'] in FINISHED for a in attempts)


def next_attempt_at(cfg: ScheduleSettings, now_local: datetime, attempts: list[dict]) -> datetime | None:
    """When the next scheduled attempt may start today (None = nothing more today)."""
    slot = _slot(cfg, now_local)
    if is_done(attempts):
        return None
    if now_local < slot or not attempts:
        return max(slot, now_local) if not attempts else slot  # first attempt: at the slot, or right away if it passed
    last = datetime.fromisoformat(attempts[-1]['started_at']).astimezone(now_local.tzinfo)
    nxt = max(now_local, last + RETRY_EVERY)
    return nxt if nxt <= slot + RETRY_WINDOW else None


def is_due(cfg: ScheduleSettings, now_local: datetime, attempts: list[dict]) -> bool:
    """Due at the daily time (or right after a restart that missed it). If the attempt fails or is interrupted it is
    retried every 5 minutes for 2 hours. Completed / partial / user-stopped runs end the day's job."""
    if not cfg.enabled or now_local < _slot(cfg, now_local) or is_done(attempts):
        return False
    if not attempts:
        return True
    nxt = next_attempt_at(cfg, now_local, attempts)
    return nxt is not None and now_local >= nxt


async def tick() -> None:
    # Must be a coroutine: APScheduler then runs it on the app's event loop, which start_run() needs.
    try:
        automation_store.mark_stale_runs_interrupted(automation_store.STALE_AFTER_SECONDS)  # runs whose process died
        settings = config_store.load_all()
        for kind, cfg in (('product', settings.product), ('blog', settings.blog), ('guide', config_store.load_guide())):
            now = runner.local_now(cfg.timezone)
            attempts = slot_attempts(kind, cfg, now)
            if is_due(cfg, now, attempts):
                try:
                    run_id = runner.start_run(kind, trigger='schedule')
                    logger.info('automation_run_started', extra={'kind': kind, 'run_id': run_id})
                except runner.RunAlreadyActive:
                    pass
    except Exception:  # noqa: BLE001 - the tick must never die
        logger.exception('automation_tick_failed')


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    interrupted = automation_store.mark_stale_runs_interrupted(automation_store.STALE_AFTER_SECONDS)
    if interrupted:
        logger.warning('automation_runs_interrupted', extra={'count': interrupted})
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(tick, 'interval', seconds=60, id='automation_tick', max_instances=1, coalesce=True)
    _scheduler.start()


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None

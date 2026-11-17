"""
app/modules/countries/scheduler.py
===================================
Automatic (timed) reading.

The admin can set a schedule — daily at a time, weekly on a day/time, or every
N hours — and which countries to read. While the app is running, APScheduler
fires the reader automatically at that time. Changing the schedule reschedules
the job immediately.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.modules.countries import reader
from app.modules.countries.models import ReaderSchedule

logger = logging.getLogger("requirement_reader")

_scheduler: AsyncIOScheduler | None = None
_JOB_ID = "reader_job"


async def _get_row(db) -> ReaderSchedule:
    row = (await db.execute(select(ReaderSchedule).limit(1))).scalar_one_or_none()
    if row is None:
        row = ReaderSchedule(id="default")
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def _scheduled_read() -> None:
    """The job body: run the reader for the configured countries."""
    logger.info("Scheduled read starting…")
    async with AsyncSessionLocal() as db:
        row = await _get_row(db)
        codes = None if row.countries.upper() == "ALL" else [
            c.strip() for c in row.countries.split(",") if c.strip()]
        result = await reader.run_reader(db, countries=codes)
        row.last_run_at = datetime.now()
        await db.commit()
    logger.info("Scheduled read done: %s", result["message"])
    # refresh next_run_at
    await _persist_next_run()


def _build_trigger(row: ReaderSchedule):
    tz = row.timezone or "Asia/Karachi"
    hh, mm = (row.at_time or "02:00").split(":")
    if row.frequency == "once":
        if not row.run_date:
            return None
        try:
            run_dt = datetime(row.run_date.year, row.run_date.month, row.run_date.day,
                              int(hh), int(mm), tzinfo=ZoneInfo(tz))
        except Exception:
            return None
        return DateTrigger(run_date=run_dt)
    if row.frequency == "interval":
        return IntervalTrigger(hours=max(1, row.interval_hours or 24), timezone=tz)
    if row.frequency == "weekly":
        return CronTrigger(day_of_week=row.day_of_week or 0, hour=int(hh), minute=int(mm), timezone=tz)
    return CronTrigger(hour=int(hh), minute=int(mm), timezone=tz)   # daily


async def _persist_next_run() -> None:
    job = _scheduler.get_job(_JOB_ID) if _scheduler else None
    nxt = job.next_run_time if job else None
    async with AsyncSessionLocal() as db:
        row = await _get_row(db)
        row.next_run_at = nxt
        await db.commit()


async def reschedule() -> None:
    """(Re)register the job from the current DB schedule. Call after any change."""
    if _scheduler is None:
        return
    if _scheduler.get_job(_JOB_ID):
        _scheduler.remove_job(_JOB_ID)
    async with AsyncSessionLocal() as db:
        row = await _get_row(db)
        enabled = row.enabled
        trigger = _build_trigger(row) if enabled else None
    if enabled:
        _scheduler.add_job(_scheduled_read, trigger=trigger, id=_JOB_ID,
                           replace_existing=True, max_instances=1, coalesce=True)
        logger.info("Reader schedule active.")
    else:
        logger.info("Reader schedule disabled.")
    await _persist_next_run()


async def start() -> None:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
        _scheduler.start()
    await reschedule()


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None

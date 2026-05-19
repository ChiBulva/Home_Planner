from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Task, utcnow
from app.realtime import manager


def parse_reset_time(value: str) -> tuple[int, int]:
    try:
        if value.startswith("days="):
            value = value.split("time=", 1)[1]
        hour, minute = value.split(":", 1)
        return max(0, min(23, int(hour))), max(0, min(59, int(minute[:2])))
    except (IndexError, ValueError):
        return settings.reset_hour, settings.reset_minute


def parse_weekdays(value: str) -> set[int]:
    if value.startswith("days="):
        value = value.split(";", 1)[0].removeprefix("days=")
    return {int(part) for part in value.split(",") if part.strip().isdigit()}


def next_reset_time(
    frequency: str,
    value: str = "",
    *,
    now: datetime | None = None,
) -> datetime | None:
    now = now or utcnow()
    hour, minute = parse_reset_time(value)
    base = now.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )

    if frequency == "none":
        return None
    if frequency == "daily":
        return base if base > now else base + timedelta(days=1)
    if frequency == "weekly":
        return base + timedelta(days=7 if base <= now else 0)
    if frequency == "monthly":
        month = base.month + 1 if base <= now else base.month
        year = base.year + (1 if month == 13 else 0)
        month = 1 if month == 13 else month
        day = min(base.day, 28)
        return base.replace(year=year, month=month, day=day)
    if frequency == "interval":
        try:
            days = max(1, int(value))
        except ValueError:
            days = 1
        return base + timedelta(days=days if base <= now else 0)
    if frequency == "weekdays":
        wanted = parse_weekdays(value)
        if not wanted:
            wanted = {0, 1, 2, 3, 4, 5, 6}
        for offset in range(0, 8):
            candidate = base + timedelta(days=offset)
            if candidate > now and candidate.weekday() in wanted:
                return candidate
    return None


async def reset_due_tasks() -> None:
    changed = False
    now = utcnow()
    with SessionLocal() as db:
        tasks = db.scalars(
            select(Task).where(Task.next_reset_at.is_not(None), Task.next_reset_at <= now)
        ).all()
        for task in tasks:
            task.status = "incomplete"
            task.next_reset_at = next_reset_time(task.reset_frequency, task.reset_value, now=now)
            task.updated_at = now
            changed = True
        db.commit()
    if changed:
        await manager.broadcast("tasks_changed")


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=timezone.utc)
    scheduler.add_job(lambda: asyncio.create_task(reset_due_tasks()), "interval", minutes=1)
    scheduler.start()
    return scheduler

"""
IM8 Bot — Task Scheduler
APScheduler integration for recurring and one-off automated tasks.
"""

import logging
from datetime import datetime
from typing import Callable, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger("im8bot.scheduler")


class Scheduler:
    """Wrapper around APScheduler's AsyncIOScheduler for IM8 Bot."""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 3,
                "misfire_grace_time": 60,
            }
        )
        self._is_running: bool = False

    # ═══════════════════════════════════════════════
    #  Lifecycle
    # ═══════════════════════════════════════════════

    def start(self) -> None:
        """Starts the scheduler."""
        if not self._is_running:
            self._scheduler.start()
            self._is_running = True
            logger.info("Scheduler started.")

    def shutdown(self) -> None:
        """Gracefully shuts down the scheduler."""
        if self._is_running:
            self._scheduler.shutdown(wait=False)
            self._is_running = False
            logger.info("Scheduler stopped.")

    @property
    def is_running(self) -> bool:
        return self._is_running

    # ═══════════════════════════════════════════════
    #  Job Management
    # ═══════════════════════════════════════════════

    def add_interval_job(
        self,
        func: Callable,
        job_id: str,
        seconds: int = 0,
        minutes: int = 0,
        hours: int = 0,
        **kwargs: Any,
    ) -> None:
        """Adds a recurring job that runs at a fixed interval."""
        self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(
                seconds=seconds, minutes=minutes, hours=hours
            ),
            id=job_id,
            replace_existing=True,
            **kwargs,
        )
        logger.info(f"Interval job added: {job_id}")

    def add_cron_job(
        self,
        func: Callable,
        job_id: str,
        cron_expression: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Adds a recurring job using a cron-like schedule."""
        trigger = CronTrigger.from_crontab(cron_expression) if cron_expression else CronTrigger()
        self._scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            **kwargs,
        )
        logger.info(f"Cron job added: {job_id}")

    def add_date_job(
        self,
        func: Callable,
        job_id: str,
        run_date: datetime,
        **kwargs: Any,
    ) -> None:
        """Adds a one-off job that runs at a specific datetime."""
        self._scheduler.add_job(
            func,
            trigger=DateTrigger(run_date=run_date),
            id=job_id,
            replace_existing=True,
            **kwargs,
        )
        logger.info(f"Date job scheduled: {job_id} at {run_date}")

    def remove_job(self, job_id: str) -> bool:
        """Removes a scheduled job by ID. Returns True if found and removed."""
        try:
            self._scheduler.remove_job(job_id)
            logger.info(f"Job removed: {job_id}")
            return True
        except Exception:
            logger.warning(f"Job not found for removal: {job_id}")
            return False

    def get_jobs(self) -> list:
        """Returns a list of all scheduled jobs."""
        return self._scheduler.get_jobs()

    @property
    def job_count(self) -> int:
        """Returns the number of currently scheduled jobs."""
        return len(self._scheduler.get_jobs())

import logging
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from django import db
from django.conf import settings

logger = logging.getLogger(__name__)

scheduler = BlockingScheduler(timezone=dt_timezone.utc)
scheduled_task_registry = {}


def get_jobs_retry_when_db_error():
    """Jobs to reschedule after a database error, as {job_id: delay_in_minutes}.

    Read on each event rather than at import time so that settings changes and
    override_settings are honoured.
    """
    return getattr(settings, "JOBS_RETRY_WHEN_DB_ERROR", {})


def db_error_listener(event):
    if isinstance(event.exception, (db.OperationalError, db.InterfaceError)):
        logger.info('DB connection error. Close old connections')
        db.close_old_connections()

        retry_delays = get_jobs_retry_when_db_error()
        if event.job_id in retry_delays:
            delay = retry_delays[event.job_id]
            logger.warning('Retry job: %s after %s min', event.job_id, delay)
            scheduler.modify_job(
                event.job_id,
                next_run_time=datetime.now(tz=dt_timezone.utc) + timedelta(minutes=delay),
            )


def scheduled_task(id, trigger=None, cron=None, **schedule_args):
    def decorator(func):
        if id in scheduled_task_registry:
            raise Exception(f'Scheduled task with id {id} already register')

        scheduled_task_registry[id] = {
            'func': func,
            'trigger': trigger,
            'cron': cron,
            'schedule_args': schedule_args
        }
        return func
    return decorator

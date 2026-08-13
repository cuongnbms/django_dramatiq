from datetime import timezone as dt_timezone

import pytest
from django.test import override_settings

from django_dramatiq import scheduler as scheduler_module
from django_dramatiq.scheduler import get_jobs_retry_when_db_error, scheduled_task, scheduled_task_registry


def test_scheduler_does_not_depend_on_pytz():
    # pytz was imported but never declared as a dependency, so the scheduler
    # crashed on a clean install. The stdlib timezone works with APScheduler.
    assert not hasattr(scheduler_module, "pytz")
    assert scheduler_module.scheduler.timezone is dt_timezone.utc


def test_retry_settings_are_read_at_runtime():
    # Previously read at import time, so override_settings had no effect.
    assert get_jobs_retry_when_db_error() == {}

    with override_settings(JOBS_RETRY_WHEN_DB_ERROR={"job-a": 5}):
        assert get_jobs_retry_when_db_error() == {"job-a": 5}

    assert get_jobs_retry_when_db_error() == {}


def test_scheduled_task_registers_once():
    try:
        @scheduled_task("test-job", cron="0 * * * *")
        def job():
            pass

        assert scheduled_task_registry["test-job"]["cron"] == "0 * * * *"

        with pytest.raises(Exception, match="already register"):
            @scheduled_task("test-job")
            def duplicate():
                pass
    finally:
        scheduled_task_registry.pop("test-job", None)


def test_db_error_listener_reschedules_configured_jobs(monkeypatch):
    from django import db

    modified = {}

    def fake_modify_job(job_id, next_run_time=None):
        modified["job_id"] = job_id
        modified["next_run_time"] = next_run_time

    monkeypatch.setattr(scheduler_module.scheduler, "modify_job", fake_modify_job)
    monkeypatch.setattr(db, "close_old_connections", lambda: modified.setdefault("closed", True))

    class Event:
        exception = db.OperationalError("connection lost")
        job_id = "job-a"

    with override_settings(JOBS_RETRY_WHEN_DB_ERROR={"job-a": 5}):
        scheduler_module.db_error_listener(Event())

    assert modified["closed"] is True
    assert modified["job_id"] == "job-a"
    assert modified["next_run_time"].tzinfo is not None


def test_db_error_listener_ignores_unrelated_exceptions(monkeypatch):
    called = []
    monkeypatch.setattr(scheduler_module.scheduler, "modify_job", lambda *a, **k: called.append(a))

    class Event:
        exception = ValueError("not a db error")
        job_id = "job-a"

    scheduler_module.db_error_listener(Event())
    assert called == []

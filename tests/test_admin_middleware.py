from threading import Event

import dramatiq
import pytest
from dramatiq import Middleware
from dramatiq.middleware import SkipMessage

from django_dramatiq.models import Task


def test_admin_middleware_keeps_track_of_tasks(transactional_db, broker, worker):
    # Given an actor
    evt = Event()

    @dramatiq.actor
    def do_work():
        evt.set()

    # When I send it a delayed message
    do_work.send_with_options(delay=250)

    # Then a Task should be stored to the database
    task = Task.tasks.get()
    assert task
    assert task.status == Task.STATUS_DELAYED

    # When I join on the broker
    evt.wait()
    broker.join(do_work.queue_name, fail_fast=True)
    worker.join()

    # And reload the task
    task.refresh_from_db()

    # Then the Task's status should be updated
    assert task.status == Task.STATUS_DONE


@pytest.mark.skip(reason="flaky due to SQLite concurrency issues with select_for_update")
def test_admin_middleware_keeps_track_of_failed_tasks(transactional_db, broker, worker):
    # Given an actor that always fails
    @dramatiq.actor(max_retries=0)
    def do_work():
        raise RuntimeError("failed")

    # When I send it a message
    do_work.send()

    # And I join on the broker
    broker.join(do_work.queue_name)
    worker.join()

    # Then a failed Task should be stored to the database
    task = Task.tasks.get()
    assert task
    assert task.status == Task.STATUS_FAILED
    assert "RuntimeError" in task.message.options["traceback"]


def test_admin_middleware_keeps_track_of_skipped_tasks(transactional_db, broker, worker):
    # Given an actor that does nothing
    @dramatiq.actor(max_retries=0)
    def do_work():
        pass

    # And a middleware that skips all messages
    class Skipper(Middleware):
        def before_process_message(self, broker, message):
            raise SkipMessage()

    # When I enable the middleware
    broker.add_middleware(Skipper())

    # And I send the actor a message
    do_work.send()

    # And I join on the broker
    broker.join(do_work.queue_name)
    worker.join()

    # Then a skipped Task should be stored to the database
    task = Task.tasks.get()
    assert task
    assert task.status == Task.STATUS_SKIPPED


def test_admin_middleware_reads_ignore_settings_at_runtime(transactional_db, broker):
    from django.test import override_settings

    from django_dramatiq.middleware import AdminMiddleware
    from tests.testapp1.tasks import example

    middleware = AdminMiddleware()
    message = example.message(1)

    # The ignore lists used to be read at import time, which meant
    # override_settings and any runtime change were silently ignored.
    with override_settings(DRAMATIQ_ADMIN_IGNORE_TASKS=[message.actor_name]):
        middleware.after_enqueue(broker, message, 0)
        assert Task.tasks.count() == 0

    with override_settings(DRAMATIQ_ADMIN_IGNORE_QUEUES=[message.queue_name]):
        middleware.after_enqueue(broker, message, 0)
        assert Task.tasks.count() == 0

    middleware.after_enqueue(broker, message, 0)
    assert Task.tasks.count() == 1


def test_admin_middleware_records_integer_duration(transactional_db, broker):
    from django_dramatiq.middleware import AdminMiddleware
    from tests.testapp1.tasks import example

    middleware = AdminMiddleware()
    message = example.message(1)

    middleware.after_enqueue(broker, message, 0)
    middleware.before_process_message(broker, message)
    middleware.after_process_message(broker, message)

    task = Task.tasks.get()
    # duration and wait_time are PositiveIntegerFields; a float would be
    # silently truncated by some backends and stored verbatim by others.
    assert isinstance(task.duration, int)
    assert task.duration >= 0
    assert isinstance(task.wait_time, int)
    assert task.wait_time >= 0


def test_duration_update_does_not_rewrite_the_message_blob(transactional_db, broker):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from django_dramatiq.middleware import AdminMiddleware
    from tests.testapp1.tasks import example

    middleware = AdminMiddleware()
    message = example.message(1, payload="x" * 1000)

    middleware.after_enqueue(broker, message, 0)
    middleware.before_process_message(broker, message)

    with CaptureQueriesContext(connection) as ctx:
        middleware.after_process_message(broker, message)

    updates = [q["sql"] for q in ctx.captured_queries if q["sql"].lstrip().upper().startswith("UPDATE")]
    # Two updates: the status/end_at write, then a narrow duration-only write
    # that must not carry the blob along with it.
    assert len(updates) == 2, updates
    assert "message_data" not in updates[-1], updates[-1]


@pytest.mark.parametrize("elapsed,expected", [(0.2, 1), (1.0, 1), (9.5, 10), (10.0, 10)])
def test_duration_rounds_up_to_whole_seconds(transactional_db, broker, monkeypatch, elapsed, expected):
    from datetime import timedelta

    from django.utils import timezone as dj_timezone

    from django_dramatiq import middleware as middleware_module
    from django_dramatiq.middleware import AdminMiddleware
    from tests.testapp1.tasks import example

    started = dj_timezone.now()
    middleware = AdminMiddleware()
    message = example.message(1)

    monkeypatch.setattr(middleware_module.timezone, "now", lambda: started)
    middleware.after_enqueue(broker, message, 0)
    middleware.before_process_message(broker, message)

    monkeypatch.setattr(middleware_module.timezone, "now", lambda: started + timedelta(seconds=elapsed))
    middleware.after_process_message(broker, message)

    # The old formula was total_seconds() + 1, which inflated every duration by
    # a whole second. Rounding up keeps sub-second tasks from reporting 0
    # without distorting longer ones.
    assert Task.tasks.get().duration == expected


def test_str_does_not_refetch_the_deferred_blob(transactional_db, broker):
    from django.contrib.admin.sites import AdminSite
    from django.contrib.auth.models import AnonymousUser
    from django.db import connection
    from django.test import RequestFactory
    from django.test.utils import CaptureQueriesContext

    from django_dramatiq.admin import TaskAdmin
    from tests.testapp1.tasks import example

    for i in range(10):
        Task.tasks.create_or_update_from_message(example.message(i), status=Task.STATUS_DONE)

    request = RequestFactory().get("/admin/django_dramatiq/task/")
    request.user = AnonymousUser()
    rows = list(TaskAdmin(Task, AdminSite()).get_queryset(request))

    # Stringifying deferred rows used to lazy-load message_data once per row.
    with CaptureQueriesContext(connection) as ctx:
        labels = [str(task) for task in rows]

    assert len(ctx.captured_queries) == 0, [q["sql"] for q in ctx.captured_queries]
    assert all(label.startswith("example(") for label in labels), labels


def test_str_matches_the_decoded_message_representation(transactional_db, broker):
    from tests.testapp1.tasks import example

    message = example.message(1, 2, foo="bar")
    Task.tasks.create_or_update_from_message(message, status=Task.STATUS_DONE)

    loaded = Task.tasks.get()
    deferred = Task.tasks.defer("message_data").get()

    # The blob-free fallback must render the same label as decoding the message.
    assert str(loaded) == str(message)
    assert str(deferred) == str(loaded)

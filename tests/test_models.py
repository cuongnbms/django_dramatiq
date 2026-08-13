import uuid
from unittest import mock

from django_dramatiq.models import Task


def test_task_create_or_update_from_message(transactional_db, broker, worker):
    message = mock.Mock()
    message_id = uuid.uuid4()
    message.encode.return_value = b"{}"
    message.message_id = message_id
    # the manager also records retries and the args/kwargs payload
    message.options = {}
    message.args = []
    message.kwargs = {}
    # dramatiq stamps this on every message; the manager derives `eta` from it
    message.message_timestamp = 1700000000000
    # the manager derives these from the message rather than from the caller
    message.actor_name = "do_work"
    message.queue_name = "default"

    Task.tasks.create_or_update_from_message(message)

    # Created it
    t = Task.tasks.get(pk=message.message_id)
    message.encode.assert_called_once_with()
    assert t.message_data == message.encode.return_value
    # eta is stored as a column so the admin changelist does not have to decode
    # message_data for every row
    assert t.eta is not None
    assert t.eta.timestamp() == 1700000000
    # actor_name/queue_name come from the message, so a Task can be described
    # without decoding message_data
    assert t.actor_name == "do_work"
    assert t.queue_name == "default"
    message.encode.reset_mock()
    message.encode.return_value = b'{"another_one", 12}'
    Task.tasks.create_or_update_from_message(message)

    # Updated it
    t.refresh_from_db()
    message.encode.assert_called_once_with()
    assert Task.tasks.count() == 1
    assert t.message_data == message.encode.return_value


def test_delete_old_tasks_deletes_in_batches(transactional_db, broker):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from tests.testapp1.tasks import example

    for i in range(10):
        message = example.message(i)
        Task.tasks.create_or_update_from_message(message, status=Task.STATUS_DONE)

    assert Task.tasks.count() == 10

    # A single unbatched DELETE would be one long transaction holding a lock on
    # the whole table, so the manager walks the rows in bounded chunks instead.
    with CaptureQueriesContext(connection) as ctx:
        deleted = Task.tasks.delete_old_tasks(-100, batch_size=4)

    assert deleted == 10
    assert Task.tasks.count() == 0

    deletes = [q for q in ctx.captured_queries if q["sql"].lstrip().upper().startswith("DELETE")]
    assert len(deletes) == 3, [q["sql"] for q in deletes]


def test_delete_old_tasks_keeps_recent_tasks(transactional_db, broker):
    from tests.testapp1.tasks import example

    for i in range(3):
        Task.tasks.create_or_update_from_message(example.message(i), status=Task.STATUS_DONE)

    # Nothing is older than an hour, so nothing should go.
    assert Task.tasks.delete_old_tasks(3600) == 0
    assert Task.tasks.count() == 3

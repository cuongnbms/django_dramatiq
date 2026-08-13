import html
import json

from django.contrib.admin.sites import AdminSite

from django_dramatiq.admin import TaskAdmin
from django_dramatiq.apps import DjangoDramatiqConfig
from django_dramatiq.encoders import JSONEncoder
from django_dramatiq.models import Task


def _task_for(message):
    return Task(id=message.message_id, message_data=message.encode())


def test_default_encoder_is_recognised_as_json():
    # The admin decides whether to show real args/kwargs by checking the
    # configured encoder against dramatiq's JSONEncoder, so ours must be one.
    from dramatiq.encoder import JSONEncoder as DramatiqJSONEncoder

    assert isinstance(DjangoDramatiqConfig.select_encoder(), DramatiqJSONEncoder)
    assert isinstance(JSONEncoder(), DramatiqJSONEncoder)


def test_message_details_shows_real_args_with_default_encoder(broker):
    from tests.testapp1.tasks import example

    message = example.message(1, 2, foo="bar")
    admin = TaskAdmin(Task, AdminSite())

    rendered = str(admin.message_details(_task_for(message)))
    details = json.loads(html.unescape(rendered.removeprefix("<pre>").removesuffix("</pre>")))

    # args/kwargs must survive verbatim, not be replaced with "<...>" placeholders
    assert details["args"] == [1, 2]
    assert details["kwargs"] == {"foo": "bar"}


def test_message_details_escapes_html(broker):
    from tests.testapp1.tasks import example

    message = example.message("<script>alert(1)</script>")
    admin = TaskAdmin(Task, AdminSite())

    rendered = str(admin.message_details(_task_for(message)))

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_message_details_masks_args_with_non_json_encoder(broker, monkeypatch):
    # With a non-JSON encoder the payload can't be shown verbatim, so the admin
    # substitutes placeholders. args is a list and kwargs a dict.
    from dramatiq.encoder import PickleEncoder

    from tests.testapp1.tasks import example

    monkeypatch.setattr(DjangoDramatiqConfig, "select_encoder", classmethod(lambda cls: PickleEncoder()))

    message = example.message(1, 2, foo="bar")
    admin = TaskAdmin(Task, AdminSite())

    rendered = str(admin.message_details(_task_for(message)))
    details = json.loads(html.unescape(rendered.removeprefix("<pre>").removesuffix("</pre>")))

    assert details["args"] == ["<1>", "<2>"]
    assert details["kwargs"] == {"foo": "<bar>"}


def test_change_page_renders(admin_client, broker):
    # Every non-editable field named in fieldsets must render on the change
    # page; a mismatch between fieldsets and readonly_fields blows it up.
    import uuid

    from tests.testapp1.tasks import example

    message = example.message(1, foo="bar")
    task = Task(
        id=uuid.uuid4(),
        message_data=message.encode(),
        actor_name="example",
        queue_name="default",
        params={"args": [1], "kwargs": {"foo": "bar"}},
    )
    task.save()

    response = admin_client.get(f"/admin/django_dramatiq/task/{task.id}/change/")

    assert response.status_code == 200
    content = response.content.decode()
    assert "example" in content
    assert "Timeline" in content


def test_changelist_renders(admin_client, broker):
    response = admin_client.get("/admin/django_dramatiq/task/")

    assert response.status_code == 200


def _admin_request():
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    request = RequestFactory().get("/admin/django_dramatiq/task/")
    request.user = AnonymousUser()
    return request


def test_changelist_queryset_does_not_load_message_data(transactional_db, broker):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from tests.testapp1.tasks import example

    for i in range(5):
        Task.tasks.create_or_update_from_message(
            example.message(i, payload="x" * 500), status=Task.STATUS_DONE
        )

    admin = TaskAdmin(Task, AdminSite())

    with CaptureQueriesContext(connection) as ctx:
        rows = list(admin.get_queryset(_admin_request()))

    assert len(rows) == 5
    # The blob must not be pulled over the wire for every changelist row.
    selects = [q["sql"] for q in ctx.captured_queries if q["sql"].lstrip().upper().startswith("SELECT")]
    assert selects
    assert all("message_data" not in sql for sql in selects), selects


def test_changelist_renders_eta_without_decoding_the_message(transactional_db, broker):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from tests.testapp1.tasks import example

    message = example.message(1)
    Task.tasks.create_or_update_from_message(message, status=Task.STATUS_DONE)

    admin = TaskAdmin(Task, AdminSite())
    task = admin.get_queryset(_admin_request()).get()

    # eta comes from its own column now, so reading it must not trigger the
    # lazy load of the deferred blob.
    with CaptureQueriesContext(connection) as ctx:
        eta = task.eta

    assert eta is not None
    assert eta.timestamp() == message.message_timestamp / 1000
    assert len(ctx.captured_queries) == 0


def test_detail_view_can_still_render_message_details(transactional_db, broker):
    from tests.testapp1.tasks import example

    message = example.message(1, 2, foo="bar")
    Task.tasks.create_or_update_from_message(message, status=Task.STATUS_DONE)

    admin = TaskAdmin(Task, AdminSite())
    # Deferring the blob must not break the detail page: it loads lazily.
    task = admin.get_queryset(_admin_request()).get()
    rendered = str(admin.message_details(task))

    details = json.loads(html.unescape(rendered.removeprefix("<pre>").removesuffix("</pre>")))
    assert details["args"] == [1, 2]
    assert details["kwargs"] == {"foo": "bar"}

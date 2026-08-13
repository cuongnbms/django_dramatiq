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

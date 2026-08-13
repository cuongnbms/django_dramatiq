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

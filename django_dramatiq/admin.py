import json

from django.contrib import admin
from django.utils.html import format_html
from django_dramatiq.apps import DjangoDramatiqConfig
from dramatiq.encoder import JSONEncoder

from .models import Task
from .utils import display_diff_time


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    exclude = ("message_data",)
    readonly_fields = (
        "message_details",
        "traceback",
        "status",
        "retries",
        "queue_name",
        "actor_name",
        "display_params",
    )
    list_display = (
        "id",
        "actor_name",
        "display_params",
        "display_status",
        "display_wait_time",
        "retries",
        "display_duration",
        "queue_name",
        "eta",
        "created_at",
        "updated_at",

    )
    list_filter = ("status", "created_at", "queue_name", "actor_name")
    search_fields = ("actor_name",)

    fieldsets = (
        (
            None,
            {
                'fields': (
                    'id', ('status', 'retries'), ('actor_name', 'queue_name'), 'display_params',
                )
            }
        ),

        (
            'Timeline',
            {
                'fields': (
                    ('created_at', 'eta'), ('start_at', 'end_at'), ('updated_at', 'duration'), ('wait_time',)
                )
            }
        ),

        (
            'Details',
            {
                'fields': (
                    'message_details', 'traceback',
                )
            }
        ),

    )

    # The changelist would otherwise run a second COUNT(*) over the whole table.
    show_full_result_count = False

    def get_queryset(self, request):
        # message_data is a blob that is only rendered on the detail page. Every
        # changelist row would otherwise pull it over the wire, so defer it and
        # let the detail view load it lazily when message_details asks for it.
        return super().get_queryset(request).defer("message_data")

    def message_details(self, instance):
        message_dict = instance.message._asdict()

        # make sure we can still get a representation of the
        # args + kwargs payload when a non json encoder is in use
        dramatiq_encoder = DjangoDramatiqConfig.select_encoder()
        if not isinstance(dramatiq_encoder, JSONEncoder):
            message_dict["args"] = [f"<{v}>" for v in message_dict["args"]]
            message_dict["kwargs"] = {k: f"<{v}>" for k, v in message_dict["kwargs"].items()}

        message_details = json.dumps(message_dict, indent=4)
        return format_html("<pre>{}</pre>", message_details)

    def traceback(self, instance):
        traceback = instance.message.options.get("traceback", None)
        if traceback:
            return format_html("<pre>{}</pre>", traceback)
        return None

    @admin.display(description='Status', ordering='status')
    def display_status(self, instance):
        status = instance.status.upper()
        if status == "FAILED":
            return format_html('<b style="color:{};">{}</b>', '#f20707', status)
        if status == "DONE":
            return format_html('<b style="color:{};">{}</b>', '#3d9402', status)
        return format_html('<b style="color:{};">{}</b>', '#ffad00', status)

    @admin.display(description='Wait time', ordering='wait_time')
    def display_wait_time(self, instance):
        return display_diff_time(instance.wait_time)

    @admin.display(description='Duration', ordering='duration')
    def display_duration(self, instance):
        return display_diff_time(instance.duration)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, task=None):
        return False

    def has_delete_permission(self, request, task=None):
        return False

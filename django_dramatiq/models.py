from datetime import timedelta

from django.db import models
from django.utils.functional import cached_property
from django.utils.timezone import now
from dramatiq import Message

from .encoders import ExtendJSONEncoder


class TaskManager(models.Manager):
    def create_or_update_from_message(self, message, **extra_fields):
        retries = int(message.options.get("retries", 0))
        status = extra_fields.get('status', '')
        if retries == 0 and status == Task.STATUS_RUNNING:
            eta = message.options.get("eta", message.message_timestamp) / 1000
            wait_time = now().timestamp() - eta
            extra_fields['wait_time'] = wait_time

        task, _ = self.update_or_create(
            id=message.message_id,
            defaults={
                "message_data": message.encode(),
                "retries": retries,
                "params": {
                    "args": message.args,
                    "kwargs": message.kwargs,
                },
                **extra_fields,
            },
        )
        return task

    def delete_old_tasks(self, max_task_age):
        self.filter(created_at__lte=now() - timedelta(seconds=max_task_age)).delete()


class Task(models.Model):
    STATUS_ENQUEUED = "enqueued"
    STATUS_DELAYED = "delayed"
    STATUS_RUNNING = "running"
    STATUS_FAILED = "failed"
    STATUS_DONE = "done"
    STATUS_SKIPPED = "skipped"
    STATUSES = [
        (STATUS_ENQUEUED, "Enqueued"),
        (STATUS_DELAYED, "Delayed"),
        (STATUS_RUNNING, "Running"),
        (STATUS_FAILED, "Failed"),
        (STATUS_DONE, "Done"),
        (STATUS_SKIPPED, "Skipped"),
    ]

    id = models.UUIDField(primary_key=True, editable=False)
    status = models.CharField(max_length=8, choices=STATUSES, default=STATUS_ENQUEUED)
    retries = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    message_data = models.BinaryField()

    actor_name = models.CharField(max_length=300, null=True)
    queue_name = models.CharField(max_length=100, null=True)

    params = models.JSONField(null=True, blank=True, default=dict, encoder=ExtendJSONEncoder)

    wait_time = models.PositiveIntegerField(default=0)

    start_at = models.DateTimeField(null=True, blank=True, default=None)
    end_at = models.DateTimeField(null=True, blank=True, default=None)
    duration = models.PositiveIntegerField(null=True, blank=True, default=None)

    tasks = TaskManager()

    class Meta:
        ordering = ["-updated_at"]

    @cached_property
    def message(self):
        return Message.decode(bytes(self.message_data))

    def display_params(self):
        params = ", ".join(repr(arg) for arg in self.params.get('args', []))
        if self.params.get("kwargs", {}):
            params += ", " if params else ""
            params += ", ".join(f"{name}={value!r}" for name, value in self.params.get("kwargs").items())
        return params
    display_params.short_description = 'Params'

    def __str__(self):
        return str(self.message)

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db import models
from django.utils.functional import cached_property
from django.utils.timezone import now
from dramatiq import Message

from .encoders import ExtendJSONEncoder

# Number of rows deleted per transaction by delete_old_tasks. Keeps each
# statement short enough that it does not hold a lock on the whole table.
DELETE_BATCH_SIZE = 1000


def message_eta(message):
    """The moment a message is scheduled to run, as a datetime.

    Delayed messages carry an explicit "eta" option; everything else runs as
    soon as it is enqueued. Django expects an aware datetime when USE_TZ is on
    and a naive one in localtime otherwise, which is what fromtimestamp does
    when tz is None.
    """
    timestamp = message.options.get("eta", message.message_timestamp) / 1000
    tz = dt_timezone.utc if settings.USE_TZ else None
    return datetime.fromtimestamp(timestamp, tz=tz)


class TaskManager(models.Manager):
    def create_or_update_from_message(self, message, **extra_fields):
        retries = int(message.options.get("retries", 0))
        status = extra_fields.get('status', '')
        eta = message_eta(message)
        if retries == 0 and status == Task.STATUS_RUNNING:
            # wait_time is a PositiveIntegerField, so it has to be a non-negative
            # int. Clock skew between the enqueueing host and this one can make
            # the difference negative, which the database would reject.
            wait_time = now() - eta
            extra_fields['wait_time'] = max(0, int(wait_time.total_seconds()))

        task, _ = self.update_or_create(
            id=message.message_id,
            defaults={
                "message_data": message.encode(),
                "retries": retries,
                "eta": eta,
                # Derived from the message rather than left to each caller, so a
                # Task always carries enough to be described without decoding
                # the message_data blob.
                "actor_name": message.actor_name,
                "queue_name": message.queue_name,
                "params": {
                    "args": message.args,
                    "kwargs": message.kwargs,
                },
                **extra_fields,
            },
        )
        return task

    def delete_old_tasks(self, max_task_age, batch_size=DELETE_BATCH_SIZE):
        """Delete tasks created more than `max_task_age` seconds ago.

        Deletes in batches rather than with a single statement: Task has no
        inbound relations or delete signals, so Django takes the fast path and
        emits one DELETE, but on a large table that one statement is a long
        transaction holding a lock the whole time.

        Returns the number of rows deleted.
        """
        cutoff = now() - timedelta(seconds=max_task_age)
        deleted_total = 0

        while True:
            pks = list(self.filter(created_at__lte=cutoff).values_list("pk", flat=True)[:batch_size])
            if not pks:
                break

            deleted, _ = self.filter(pk__in=pks).delete()
            deleted_total += deleted
            if len(pks) < batch_size:
                break

        return deleted_total


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

    eta = models.DateTimeField(null=True, blank=True, default=None)
    start_at = models.DateTimeField(null=True, blank=True, default=None)
    end_at = models.DateTimeField(null=True, blank=True, default=None)
    duration = models.PositiveIntegerField(null=True, blank=True, default=None)

    tasks = TaskManager()

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            # delete_old_tasks filters on created_at, and the admin offers it as
            # a filter; without this the cleanup task scans the whole table.
            models.Index(fields=["created_at"], name="dd_task_created_at_idx"),
            # The admin's list_filter columns, each paired with the default
            # ordering so a filtered changelist can be served from the index.
            models.Index(fields=["status", "-updated_at"], name="dd_task_status_updated_idx"),
            models.Index(fields=["actor_name", "-updated_at"], name="dd_task_actor_updated_idx"),
            models.Index(fields=["queue_name", "-updated_at"], name="dd_task_queue_updated_idx"),
        ]

    @cached_property
    def message(self):
        return Message.decode(bytes(self.message_data))

    def display_params(self):
        # params is nullable, and rows created before it existed have no value.
        stored = self.params or {}
        params = ", ".join(repr(arg) for arg in stored.get('args', []))
        if stored.get("kwargs"):
            params += ", " if params else ""
            params += ", ".join(f"{name}={value!r}" for name, value in stored["kwargs"].items())
        return params
    display_params.short_description = 'Params'

    def __str__(self):
        # The admin defers message_data, so decoding the message here would
        # refetch the blob once per row on any page that renders several tasks.
        # actor_name and params carry the same information already.
        if "message_data" in self.get_deferred_fields():
            return f"{self.actor_name}({self.display_params()})"
        return str(self.message)

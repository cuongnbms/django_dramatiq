import logging
import math

from django import db
from django.conf import settings
from django.utils import timezone
from dramatiq.middleware import Middleware

LOGGER = logging.getLogger("django_dramatiq.AdminMiddleware")


class AdminMiddleware(Middleware):
    """This middleware keeps track of task executions."""

    def _ignore_messages(self, message):
        # Read the settings on each call rather than at import time so that
        # override_settings and runtime changes are honoured.
        if message.queue_name in getattr(settings, "DRAMATIQ_ADMIN_IGNORE_QUEUES", ()):
            return True
        if message.actor_name in getattr(settings, "DRAMATIQ_ADMIN_IGNORE_TASKS", ()):
            return True
        return False

    def after_enqueue(self, broker, message, delay):
        if self._ignore_messages(message):
            return

        from .models import Task

        LOGGER.debug("Creating Task from message %r.", message.message_id)
        status = Task.STATUS_ENQUEUED
        if delay:
            status = Task.STATUS_DELAYED

        Task.tasks.create_or_update_from_message(
            message,
            status=status,
        )

    def before_process_message(self, broker, message):
        if self._ignore_messages(message):
            return

        from .models import Task

        LOGGER.debug("Updating Task from message %r.", message.message_id)

        start_at = timezone.now()
        Task.tasks.create_or_update_from_message(
            message,
            status=Task.STATUS_RUNNING,
            start_at=start_at,
        )

    def after_skip_message(self, broker, message):
        from .models import Task

        self.after_process_message(broker, message, status=Task.STATUS_SKIPPED)

    def after_process_message(self, broker, message, *, result=None, exception=None, status=None):
        if self._ignore_messages(message):
            return

        from .models import Task

        if exception is not None:
            status = Task.STATUS_FAILED
        elif status is None:
            status = Task.STATUS_DONE

        LOGGER.debug("Updating Task from message %r.", message.message_id)
        end_at = timezone.now()
        task = Task.tasks.create_or_update_from_message(
            message,
            status=status,
            end_at=end_at,
        )
        if task.start_at:
            # duration is a PositiveIntegerField, so round up: a task that took
            # any measurable time should report at least one second rather than
            # being truncated to zero.
            task.duration = math.ceil((end_at - task.start_at).total_seconds())
            # Only write the column that changed. A bare save() would rewrite
            # the whole row, message_data blob included.
            task.save(update_fields=["duration"])


class DbConnectionsMiddleware(Middleware):
    """This middleware cleans up db connections on worker shutdown."""

    def _close_old_connections(self, *args, **kwargs):
        db.close_old_connections()

    before_process_message = _close_old_connections
    after_process_message = _close_old_connections

    def _close_connections(self, *args, **kwargs):
        db.connections.close_all()

    before_consumer_thread_shutdown = _close_connections
    before_worker_thread_shutdown = _close_connections
    before_worker_shutdown = _close_connections

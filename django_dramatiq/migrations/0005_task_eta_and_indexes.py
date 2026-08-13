"""Add the eta column and the indexes the admin and cleanup task rely on.

Before this migration the only index on the table was updated_at, so filtering
by status/actor_name/queue_name and the created_at lookup in
Task.tasks.delete_old_tasks() both scanned the whole table.

NOTE for large deployments: creating an index takes an ACCESS EXCLUSIVE lock for
the duration on PostgreSQL, which blocks writes to the task table. If your task
table is big, apply the indexes out of band with CREATE INDEX CONCURRENTLY and
then run this migration with --fake, or use
django.contrib.postgres.operations.AddIndexConcurrently in a custom migration.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('django_dramatiq', '0004_task_duration_task_end_at_task_params_task_retries_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='eta',
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AddIndex(
            model_name='task',
            index=models.Index(fields=['created_at'], name='dd_task_created_at_idx'),
        ),
        migrations.AddIndex(
            model_name='task',
            index=models.Index(fields=['status', '-updated_at'], name='dd_task_status_updated_idx'),
        ),
        migrations.AddIndex(
            model_name='task',
            index=models.Index(fields=['actor_name', '-updated_at'], name='dd_task_actor_updated_idx'),
        ),
        migrations.AddIndex(
            model_name='task',
            index=models.Index(fields=['queue_name', '-updated_at'], name='dd_task_queue_updated_idx'),
        ),
    ]

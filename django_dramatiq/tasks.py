import dramatiq


@dramatiq.actor
def delete_old_tasks(max_task_age=7 * 86400): # 7 days
    """This task deletes all tasks older than `max_task_age` from the
    database.
    """
    from .models import Task

    Task.tasks.delete_old_tasks(max_task_age)

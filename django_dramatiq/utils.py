import logging
import os

from django.utils.module_loading import import_string
import dramatiq


def getenv_int(varname, default=None):
    """Retrieves an environment variable as an int."""
    envstr = os.getenv(varname, None)

    if envstr is not None:
        try:
            return int(envstr)
        except ValueError:
            if default is None:
                raise
            msgf = "Invalid value for %s: %r. Reverting to default."
            logging.warning(msgf, varname, envstr)

    if callable(default):
        return default()
    else:
        return default


def load_middleware(path_or_obj, **kwargs):
    if isinstance(path_or_obj, str):
        return import_string(path_or_obj)(**kwargs)
    return path_or_obj

def send_task(actor_name, queue_name='default', args=None, kwargs=None):
    args = args if args else ()
    kwargs = kwargs if kwargs else {}

    message = dramatiq.Message(
        queue_name=queue_name,
        actor_name=actor_name,
        args=args,
        kwargs=kwargs,
        options={}
    )
    broker = dramatiq.get_broker()
    broker.enqueue(message)


def display_diff_time(diff_time_in_seconds, short=True):
    if diff_time_in_seconds is None:
        return '-'

    if short:
        seconds_suffix = 's'
        minutes_suffix = 'm'
        hours_suffix = 'h'
    else:
        seconds_suffix = 'seconds'
        minutes_suffix = 'minutes'
        hours_suffix = 'hours'

    if diff_time_in_seconds < 60:
        result = f'{diff_time_in_seconds} {seconds_suffix}'
    elif diff_time_in_seconds < 3600:
        minutes = int(diff_time_in_seconds / 60)
        seconds = diff_time_in_seconds - minutes * 60
        result = f'{minutes} {minutes_suffix} {seconds} {seconds_suffix}'
    else:
        hours = int(diff_time_in_seconds / 3600)
        minutes = int((diff_time_in_seconds - hours * 3600) / 60)
        result = f'{hours} {hours_suffix} {minutes} {minutes_suffix}'

    if short:
        return result.replace(' ', '')
    return result

from time import sleep

from django.conf import settings
from django.core.management import BaseCommand, CommandError
from django.utils.timezone import now

# Number of keys SCAN is asked for per round-trip.
SCAN_COUNT = 500


class Command(BaseCommand):
    help = "Displays queue sizes for a Redis broker, refreshed on a cycle."

    def add_arguments(self, parser):
        parser.add_argument('-c', '--cycle', type=int, help='refresh cycle', default=3)

    def handle(self, *args, **options):
        cycle = options.get('cycle', 3)
        client = self._get_client()
        try:
            while True:
                try:
                    self._run(client, cycle)
                    sleep(cycle)
                except KeyboardInterrupt:
                    break
        finally:
            client.close()

    def _import_redis(self):
        try:
            import redis
        except ImportError as e:
            raise CommandError(
                "The 'redis' package is required by dramatiq_stats. "
                "Install it with: pip install django_dramatiq[redis]"
            ) from e
        return redis

    def _broker_url(self):
        try:
            return settings.DRAMATIQ_BROKER['OPTIONS']['url']
        except (AttributeError, KeyError, TypeError) as e:
            raise CommandError(
                "dramatiq_stats requires a Redis broker configured with "
                "DRAMATIQ_BROKER['OPTIONS']['url']."
            ) from e

    def _get_client(self):
        redis = self._import_redis()
        return redis.Redis.from_url(self._broker_url())

    def _run(self, client, cycle):
        # SCAN rather than KEYS: KEYS is O(N) over the whole keyspace and blocks
        # the server for the duration, which stalls every other client sharing
        # this broker. SCAN walks the keyspace in bounded chunks instead.
        counted_keys = []
        for key in client.scan_iter(match='dramatiq:*', count=SCAN_COUNT):
            key = key.decode()
            queue_type, queue_name = self._get_queue_name(key)
            if queue_name:
                counted_keys.append((key, queue_type, queue_name))

        # One round-trip for all the counts instead of one per key.
        pipe = client.pipeline(transaction=False)
        for key, queue_type, _ in counted_keys:
            if queue_type == 'XQ':
                pipe.zcard(key)
            elif queue_type == 'acks':
                pipe.scard(key)
            else:
                pipe.llen(key)
        counts = pipe.execute()

        format_row = '{:<80} {:<15}'
        queue_data = []
        processing_data = []
        for (_, queue_type, queue_name), count in zip(counted_keys, counts):
            if queue_type == 'acks':
                processing_data.append(format_row.format(f'[ACKS] {queue_name}', count))
            else:
                queue_data.append(format_row.format(f'{queue_name}', count))

        self._print_result(cycle, format_row, queue_data, processing_data)

    def _print_result(self, cycle, format_row, queue_data, processing_data):
        queue_data.sort()
        processing_data.sort()

        self._clear_terminal()
        print(f'Time {now()}. Refresh cycle {cycle}s')
        print(format_row.format('Queue', 'Count'))
        print('-' * 100)
        for row in queue_data:
            print(row)
        for row in processing_data:
            print(row)

    def _clear_terminal(self):
        print("\033[H\033[J")

    def _get_queue_name(self, key):
        dramatiq_key = key[9:]
        if not dramatiq_key.startswith('__') and not dramatiq_key.endswith('.msgs'):
            if '.' in dramatiq_key:
                queue_name, queue_type = dramatiq_key.split('.')
                return queue_type, dramatiq_key
            return 'main', dramatiq_key
        if dramatiq_key.startswith('__acks__'):
            _, worker_id, *queue_name = dramatiq_key.split('.')
            queue_name = '.'.join(queue_name)
            return 'acks', f'{worker_id} - {queue_name}'
        return None, None

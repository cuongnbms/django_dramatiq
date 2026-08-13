import pytest
from django.core.management import CommandError
from django.test import override_settings

from django_dramatiq.management.commands.dramatiq_stats import Command


class FakePipeline:
    """Records the commands queued on it so the test can assert batching."""

    def __init__(self, counts):
        self._counts = counts
        self.calls = []

    def zcard(self, key):
        self.calls.append(("zcard", key))

    def scard(self, key):
        self.calls.append(("scard", key))

    def llen(self, key):
        self.calls.append(("llen", key))

    def execute(self):
        return [self._counts.get(key, 0) for _, key in self.calls]


class FakeRedis:
    def __init__(self, keys, counts=None):
        self._keys = keys
        self._counts = counts or {}
        self.scan_calls = 0
        self.pipelines = []
        self.closed = False

    def scan_iter(self, match=None, count=None):
        self.scan_calls += 1
        for key in self._keys:
            yield key.encode()

    def keys(self, pattern):  # pragma: no cover - must never be called
        raise AssertionError("KEYS blocks the whole Redis server; use SCAN")

    def pipeline(self, transaction=False):
        pipe = FakePipeline(self._counts)
        self.pipelines.append(pipe)
        return pipe

    def close(self):
        self.closed = True


def test_run_uses_scan_and_a_single_pipeline(capsys):
    keys = [
        "dramatiq:default",
        "dramatiq:default.XQ",
        "dramatiq:__acks__.worker1.default",
        "dramatiq:default.msgs",
    ]
    client = FakeRedis(keys, counts={"dramatiq:default": 7, "dramatiq:default.XQ": 2})
    command = Command()

    command._run(client, cycle=3)

    # SCAN instead of KEYS, and every count batched into one round-trip.
    assert client.scan_calls == 1
    assert len(client.pipelines) == 1

    queued = client.pipelines[0].calls
    assert ("llen", "dramatiq:default") in queued
    assert ("zcard", "dramatiq:default.XQ") in queued
    assert ("scard", "dramatiq:__acks__.worker1.default") in queued
    # .msgs keys are not queues and must be skipped entirely
    assert all(key != "dramatiq:default.msgs" for _, key in queued)

    out = capsys.readouterr().out
    assert "dramatiq:default" not in out  # names are stripped of the prefix
    assert "7" in out


def test_handle_closes_the_client():
    client = FakeRedis(["dramatiq:default"])
    command = Command()
    command._get_client = lambda: client

    def stop(*args, **kwargs):
        raise KeyboardInterrupt

    command._run = stop
    command.handle(cycle=1)

    assert client.closed


def test_missing_redis_package_reports_how_to_install(monkeypatch):
    command = Command()

    def boom():
        raise CommandError(
            "The 'redis' package is required by dramatiq_stats. "
            "Install it with: pip install django_dramatiq[redis]"
        )

    monkeypatch.setattr(command, "_import_redis", boom)
    with pytest.raises(CommandError, match="django_dramatiq\\[redis\\]"):
        command._get_client()


@override_settings(DRAMATIQ_BROKER={"BROKER": "dramatiq.brokers.stub.StubBroker", "OPTIONS": {}})
def test_missing_broker_url_raises_command_error():
    with pytest.raises(CommandError, match="DRAMATIQ_BROKER"):
        Command()._broker_url()

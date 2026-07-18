"""Shell launch failures must be stable and free of host error details."""

import asyncio

from services.shell import service as shell_service
from services.shell.service import ShellService


def test_execute_redacts_launch_failure(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(
        shell_service,
        "report_exception",
        lambda _logger, event, _error, **kwargs: events.append((event, kwargs)),
    )

    result = asyncio.run(ShellService().execute("echo ok", cwd=str(tmp_path / "missing")))

    assert result.stderr == "Command could not be started"
    assert result.exit_code == -1
    assert events == [("shell_command_launch_failed", {"outcome": "critical"})]


def test_stream_redacts_launch_failure(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(
        shell_service,
        "report_exception",
        lambda _logger, event, _error, **kwargs: events.append((event, kwargs)),
    )
    service = ShellService()
    service.cwd = str(tmp_path / "missing")

    async def collect():
        return [item async for item in service.stream("echo ok")]

    events_out = asyncio.run(collect())

    assert events_out == [
        {"stream": "stderr", "data": "Command could not be started"},
        {"exit_code": -1},
    ]
    assert events == [("shell_stream_launch_failed", {"outcome": "critical"})]

import asyncio
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime

from gpu_monitor.collectors import ascend, nvidia
from gpu_monitor.config import ServerConfig
from gpu_monitor.models import ServerSnapshot
from gpu_monitor.ssh_client import SSHMonitorClient

_DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0
_MAX_PROBE_TIMEOUT_SECONDS = 30.0
_PROBE_TIMEOUT_POLL_INTERVAL_BUFFER_SECONDS = 1.0
_COLLECTORS = {
    "gpu": nvidia.collect,
    "npu": ascend.collect,
}


class DeviceMonitor:
    """Refreshes device metrics on each server via SSH."""

    def __init__(
        self,
        servers: Iterable[ServerConfig],
        ssh_client: SSHMonitorClient,
        *,
        poll_interval_seconds: int = 20,
    ) -> None:
        self._ssh_client = ssh_client
        self._poll_interval_seconds = poll_interval_seconds
        self._servers_by_host: dict[str, ServerConfig] = {}
        self._probe_timeout_by_host: dict[str, float] = {}
        self._snapshots: dict[str, ServerSnapshot] = {}
        default_probe_timeout = min(
            _MAX_PROBE_TIMEOUT_SECONDS,
            max(
                _DEFAULT_PROBE_TIMEOUT_SECONDS,
                float(poll_interval_seconds) - _PROBE_TIMEOUT_POLL_INTERVAL_BUFFER_SECONDS,
            ),
        )
        for server in servers:
            probe_timeout = server.ssh_options.get("ConnectTimeout", default_probe_timeout)
            try:
                probe_timeout = float(probe_timeout)
            except (TypeError, ValueError):
                probe_timeout = default_probe_timeout
            if probe_timeout <= 0:
                probe_timeout = default_probe_timeout

            self._servers_by_host[server.host] = server
            self._probe_timeout_by_host[server.host] = probe_timeout
            self._snapshots[server.host] = ServerSnapshot(
                name=server.host,
                is_stale=True,
                device_type=server.device_type,
                warnings=["Waiting for first data"],
            )
        self._lock = asyncio.Lock()
        self._in_flight: dict[str, asyncio.Task[None]] = {}
        self._last_refresh_completed_at: dict[str, float] = {}

    async def refresh_snapshot(
        self, server_name: str, *, force: bool = False
    ) -> ServerSnapshot:
        try:
            server = self._servers_by_host[server_name]
        except KeyError:
            raise KeyError(server_name) from None

        async with self._lock:
            if not force and self._snapshot_is_fresh(server.host):
                return self._snapshots[server.host]

        await self._refresh_server(server)

        async with self._lock:
            self._last_refresh_completed_at[server.host] = (
                asyncio.get_running_loop().time()
            )
            return self._snapshots[server.host]

    def _snapshot_is_fresh(self, server_name: str) -> bool:
        completed_at = self._last_refresh_completed_at.get(server_name)
        if completed_at is None:
            return False
        age = asyncio.get_running_loop().time() - completed_at
        return age < self._poll_interval_seconds

    async def _refresh_server(self, server: ServerConfig) -> None:
        async with self._lock:
            task = self._in_flight.get(server.host)
            if task is None:
                task = asyncio.create_task(
                    self._poll_once(server), name=f"device-refresh:{server.host}"
                )
                self._in_flight[server.host] = task

        try:
            await task
        finally:
            async with self._lock:
                if self._in_flight.get(server.host) is task and task.done():
                    del self._in_flight[server.host]

    async def _poll_once(self, server: ServerConfig) -> None:
        try:
            snapshot = await _COLLECTORS[server.device_type](
                server,
                self._ssh_client,
                timeout=self._probe_timeout_by_host[server.host],
            )
            snapshot = replace(snapshot, last_seen=datetime.now(UTC))
        except Exception:
            async with self._lock:
                previous = self._snapshots.get(server.host)
            if previous and previous.devices:
                snapshot = replace(
                    previous,
                    is_stale=True,
                    warnings=["Polling failed; showing last known data"],
                )
            else:
                snapshot = ServerSnapshot(
                    name=server.host,
                    last_seen=None,
                    is_stale=True,
                    device_type=server.device_type,
                    devices=[],
                    warnings=["Polling failed; no data available"],
                )
        async with self._lock:
            self._snapshots[server.host] = snapshot

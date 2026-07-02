import asyncio
import csv
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from io import StringIO

from gpu_monitor.config import ServerConfig
from gpu_monitor.ssh_client import SSHMonitorClient

_DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0
_MAX_PROBE_TIMEOUT_SECONDS = 30.0
_PROBE_TIMEOUT_POLL_INTERVAL_BUFFER_SECONDS = 1.0

_GPU_SNAPSHOT_PROBE = (
    "apps=$(nvidia-smi --query-compute-apps=pid,gpu_uuid,used_gpu_memory "
    "--format=csv,noheader,nounits 2>/dev/null || true); "
    "printf '__GPU__\\n'; "
    "nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu "
    "--format=csv,noheader,nounits; "
    "gpu_status=$?; "
    "printf '__APPS__\\n'; "
    "printf '%s\\n' \"$apps\"; "
    "printf '__PS__\\n'; "
    "pids=$(printf '%s\\n' \"$apps\" | awk -F, "
    "'{gsub(/ /,\"\",$1); if ($1 ~ /^[0-9]+$/) print $1}' | sort -u | paste -sd, -); "
    'if [ -n "$pids" ]; then '
    'ps -o pid,user --no-headers -p "$pids" 2>/dev/null || true; '
    "fi; "
    'exit "$gpu_status"'
)


@dataclass(slots=True)
class ProcessInfo:
    user: str
    memory_mb: int


@dataclass(slots=True)
class GPUStatus:
    index: int
    uuid: str
    name: str
    memory_total_mb: int
    memory_used_mb: int
    utilization_percent: int
    processes: list[ProcessInfo] = field(default_factory=list)


@dataclass(slots=True)
class ServerSnapshot:
    name: str
    last_seen: datetime | None = None
    is_stale: bool = False
    gpus: list[GPUStatus] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class GPUMonitor:
    """Refreshes GPU metrics on each server via SSH."""

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
                warnings=["Waiting for first GPU data"],
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
                    self._poll_once(server), name=f"gpu-refresh:{server.host}"
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
            snapshot = await self._collect_snapshot(server)
        except Exception:
            async with self._lock:
                previous = self._snapshots.get(server.host)
            if previous and previous.gpus:
                snapshot = replace(
                    previous,
                    is_stale=True,
                    warnings=["Polling failed; showing last known GPU data"],
                )
            else:
                snapshot = ServerSnapshot(
                    name=server.host,
                    last_seen=None,
                    is_stale=True,
                    gpus=[],
                    warnings=["Polling failed; no GPU data available"],
                )
        async with self._lock:
            self._snapshots[server.host] = snapshot

    async def _collect_snapshot(self, server: ServerConfig) -> ServerSnapshot:
        raw = await self._ssh_client.run_probe(
            server,
            _GPU_SNAPSHOT_PROBE,
            timeout=self._probe_timeout_by_host[server.host],
        )
        sections = _parse_snapshot_sections(raw)
        gpu_rows = _parse_csv_lines("\n".join(sections.get("GPU", [])))
        gpus = [gpu for row in gpu_rows if (gpu := _parse_gpu_row(row)) is not None]

        process_rows = _parse_csv_lines("\n".join(sections.get("APPS", [])))
        process_lines = sections.get("PS", [])
        processes_by_gpu = _map_process_rows(process_rows, process_lines)

        for gpu in gpus:
            gpu_processes = processes_by_gpu.get(gpu.uuid, [])
            gpu.processes = gpu_processes
            gpu.utilization_percent = min(max(gpu.utilization_percent, 0), 100)

        return ServerSnapshot(
            name=server.host,
            last_seen=datetime.now(UTC),
            gpus=gpus,
        )


def _parse_snapshot_sections(raw: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"GPU": [], "APPS": [], "PS": []}
    current: str | None = None
    for line in raw.splitlines():
        marker = line.strip()
        if marker == "__GPU__":
            current = "GPU"
            continue
        if marker == "__APPS__":
            current = "APPS"
            continue
        if marker == "__PS__":
            current = "PS"
            continue
        if current is not None and line.strip():
            sections[current].append(line)
    return sections


def _map_process_rows(
    process_rows: list[list[str]], process_lines: Iterable[str]
) -> dict[str, list[ProcessInfo]]:
    if not process_rows:
        return {}

    pid_to_gpus: dict[int, list[tuple[str, int]]] = {}
    for row in process_rows:
        try:
            pid = int(row[0])
            gpu_uuid = row[1].strip()
            memory_mb = int(row[2])
            pid_to_gpus.setdefault(pid, []).append((gpu_uuid, memory_mb))
        except (IndexError, ValueError):
            continue

    process_map: dict[str, list[ProcessInfo]] = {}
    details = _parse_ps_lines(process_lines)
    for pid, user in details.items():
        for gpu_uuid, memory_mb in pid_to_gpus.get(pid, []):
            process_map.setdefault(gpu_uuid, []).append(
                ProcessInfo(
                    user=user,
                    memory_mb=memory_mb,
                )
            )
    return process_map


def _parse_ps_lines(lines: Iterable[str]) -> dict[int, str]:
    output: dict[int, str] = {}
    for line in lines:
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        pid, user = parts[:2]
        try:
            output[int(pid)] = user
        except ValueError:
            continue
    return output


def _parse_csv_lines(raw: str) -> list[list[str]]:
    lines = []
    for row in csv.reader(StringIO(raw), skipinitialspace=True):
        if not row or not any(value.strip() for value in row):
            continue
        lines.append([value.strip() for value in row])
    return lines


def _parse_gpu_row(row: list[str]) -> GPUStatus | None:
    if len(row) < 6:
        return None
    try:
        index = int(row[0])
        memory_total_mb = int(row[3])
        memory_used_mb = int(row[4])
        utilization_percent = int(row[5])
    except ValueError:
        return None

    return GPUStatus(
        index=index,
        uuid=row[1],
        name=row[2],
        memory_total_mb=memory_total_mb,
        memory_used_mb=memory_used_mb,
        utilization_percent=utilization_percent,
        processes=[],
    )


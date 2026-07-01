import asyncio
import csv
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from io import StringIO

from gpu_monitor.config import ServerConfig
from gpu_monitor.ssh_client import SSHMonitorClient

_DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0

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
    """Periodically polls GPU metrics on each server via SSH."""

    def __init__(
        self,
        servers: Iterable[ServerConfig],
        ssh_client: SSHMonitorClient,
        *,
        poll_interval_seconds: int = 20,
    ) -> None:
        self._servers = list(servers)
        self._ssh_client = ssh_client
        self._poll_interval_seconds = poll_interval_seconds
        self._snapshots: dict[str, ServerSnapshot] = {}
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._tasks:
            return
        self._stop_event.clear()
        for server in self._servers:
            task = asyncio.create_task(
                self._poll_loop(server), name=f"gpu-poller:{server.host}"
            )
            self._tasks[server.host] = task

    async def stop(self) -> None:
        self._stop_event.set()
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def get_all_snapshots(self) -> list[ServerSnapshot]:
        async with self._lock:
            return list(self._snapshots.values())

    async def _poll_loop(self, server: ServerConfig) -> None:
        while not self._stop_event.is_set():
            await self._poll_once(server)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval_seconds
                )
            except TimeoutError:
                continue

    async def _poll_once(self, server: ServerConfig) -> None:
        try:
            snapshot = await self._collect_snapshot(server)
        except Exception:  # broad catch to keep polling alive
            async with self._lock:
                previous = self._snapshots.get(server.host)
            if previous:
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
            timeout=_probe_timeout_seconds(server),
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


def _probe_timeout_seconds(server: ServerConfig) -> float:
    timeout = server.ssh_options.get("ConnectTimeout", _DEFAULT_PROBE_TIMEOUT_SECONDS)
    try:
        parsed_timeout = float(timeout)
    except (TypeError, ValueError):
        return _DEFAULT_PROBE_TIMEOUT_SECONDS
    if parsed_timeout <= 0:
        return _DEFAULT_PROBE_TIMEOUT_SECONDS
    return parsed_timeout


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


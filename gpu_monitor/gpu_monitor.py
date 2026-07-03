import asyncio
import csv
import re
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

_NPU_SNAPSHOT_PROBE = (
    "printf '__NPU__\\n'; "
    "npu_output=$(npu-smi info 2>/dev/null); "
    "npu_status=$?; "
    "printf '%s\\n' \"$npu_output\"; "
    "printf '__NPU_PROC__\\n'; "
    'if [ "$npu_status" -eq 0 ]; then '
    "ids=$(printf '%s\\n' \"$npu_output\" | awk -F'|' "
    "'$2 ~ /^[[:space:]]*[0-9]+[[:space:]]+[^[:space:]]/ "
    "{gsub(/^[ \\t]+|[ \\t]+$/, \"\", $2); split($2, a, /[[:space:]]+/); print a[1]}' "
    "| sort -nu); "
    'for id in $ids; do '
    'printf "__NPU_ID__ %s\\n" "$id"; '
    'npu-smi info -t proc-mem -i "$id" 2>/dev/null || true; '
    "done; "
    "fi; "
    "printf '__PS__\\n'; "
    "ps -eo pid,user --no-headers 2>/dev/null || true; "
    'exit "$npu_status"'
)

_NPU_PROCESS_MARKER_PREFIX = "__NPU_ID__"


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
    device_type: str = "gpu"


@dataclass(slots=True)
class ServerSnapshot:
    name: str
    last_seen: datetime | None = None
    is_stale: bool = False
    device_type: str | None = None
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
                device_type=server.device_type,
                warnings=[f"Waiting for first {_device_label(server.device_type)} data"],
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
                    warnings=[
                        "Polling failed; showing last known "
                        f"{_device_label(previous.device_type)} data"
                    ],
                )
            else:
                snapshot = ServerSnapshot(
                    name=server.host,
                    last_seen=None,
                    is_stale=True,
                    device_type=server.device_type,
                    gpus=[],
                    warnings=[
                        "Polling failed; no "
                        f"{_device_label(server.device_type)} data available"
                    ],
                )
        async with self._lock:
            self._snapshots[server.host] = snapshot

    async def _collect_snapshot(self, server: ServerConfig) -> ServerSnapshot:
        if server.device_type == "npu":
            return await self._collect_npu_snapshot(server)
        return await self._collect_gpu_snapshot(server)

    async def _collect_gpu_snapshot(self, server: ServerConfig) -> ServerSnapshot:
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
            device_type="gpu",
            gpus=gpus,
        )

    async def _collect_npu_snapshot(self, server: ServerConfig) -> ServerSnapshot:
        raw = await self._ssh_client.run_probe(
            server,
            _NPU_SNAPSHOT_PROBE,
            timeout=self._probe_timeout_by_host[server.host],
        )
        sections = _parse_snapshot_sections(raw)
        gpus = _parse_npu_info_lines(sections.get("NPU", []))
        processes_by_npu = _map_npu_process_rows(
            sections.get("NPU_PROC", []),
            sections.get("PS", []),
        )

        for gpu in gpus:
            gpu.processes = processes_by_npu.get(gpu.uuid, [])
            gpu.utilization_percent = min(max(gpu.utilization_percent, 0), 100)

        return ServerSnapshot(
            name=server.host,
            last_seen=datetime.now(UTC),
            device_type="npu",
            gpus=gpus,
        )


def _parse_snapshot_sections(raw: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {
        "GPU": [],
        "APPS": [],
        "PS": [],
        "NPU": [],
        "NPU_PROC": [],
    }
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
        if marker == "__NPU__":
            current = "NPU"
            continue
        if marker == "__NPU_PROC__":
            current = "NPU_PROC"
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


def _parse_npu_info_lines(lines: Iterable[str]) -> list[GPUStatus]:
    rows = [_split_table_row(line) for line in lines]
    rows = [row for row in rows if row]
    gpus: list[GPUStatus] = []
    index = 0
    while index < len(rows):
        device = _parse_npu_device_row(rows[index])
        if device is None or index + 1 >= len(rows):
            index += 1
            continue

        metrics = _parse_npu_metrics_row(rows[index + 1])
        if metrics is None:
            index += 1
            continue

        npu_index, name = device
        memory_used_mb, memory_total_mb, utilization_percent = metrics
        gpus.append(
            GPUStatus(
                index=npu_index,
                uuid=f"npu-{npu_index}",
                name=name,
                memory_total_mb=memory_total_mb,
                memory_used_mb=memory_used_mb,
                utilization_percent=utilization_percent,
                processes=[],
                device_type="npu",
            )
        )
        index += 2
    return gpus


def _parse_npu_device_row(row: list[str]) -> tuple[int, str] | None:
    if not row:
        return None
    parts = row[0].split()
    if len(parts) < 2:
        return None
    try:
        npu_index = int(parts[0])
    except ValueError:
        return None
    return npu_index, " ".join(parts[1:])


def _parse_npu_metrics_row(row: list[str]) -> tuple[int, int, int] | None:
    if len(row) < 3:
        return None
    metrics = " ".join(row[2:])
    usage_pairs = re.findall(r"(\d+)\s*/\s*(\d+)", metrics)
    if not usage_pairs:
        return None
    memory_used_mb, memory_total_mb = (int(value) for value in usage_pairs[-1])
    utilization_percent = _parse_first_number_as_int(metrics)
    if utilization_percent is None:
        utilization_percent = 0
    return memory_used_mb, memory_total_mb, utilization_percent


def _map_npu_process_rows(
    process_lines: Iterable[str], ps_lines: Iterable[str]
) -> dict[str, list[ProcessInfo]]:
    pid_to_npus: dict[int, list[tuple[str, int]]] = {}
    current_npu_uuid: str | None = None
    table_header: list[str] | None = None

    for line in process_lines:
        marker = line.strip()
        if marker.startswith(_NPU_PROCESS_MARKER_PREFIX):
            current_npu_uuid = _parse_npu_process_marker(marker)
            table_header = None
            continue

        row = _split_table_row(line)
        if not row:
            continue

        lowered = [cell.lower() for cell in row]
        if any("process id" in cell or "pid" == cell for cell in lowered):
            table_header = lowered
            continue

        if current_npu_uuid is None or table_header is None:
            continue

        process = _parse_npu_process_table_row(table_header, row)
        if process is None:
            continue
        pid, memory_mb = process
        pid_to_npus.setdefault(pid, []).append((current_npu_uuid, memory_mb))

    process_map: dict[str, list[ProcessInfo]] = {}
    details = _parse_ps_lines(ps_lines)
    for pid, user in details.items():
        for npu_uuid, memory_mb in pid_to_npus.get(pid, []):
            process_map.setdefault(npu_uuid, []).append(
                ProcessInfo(user=user, memory_mb=memory_mb)
            )
    return process_map


def _parse_npu_process_marker(marker: str) -> str | None:
    parts = marker.split(None, 1)
    if len(parts) != 2:
        return None
    try:
        return f"npu-{int(parts[1])}"
    except ValueError:
        return None


def _parse_npu_process_table_row(
    header: list[str], row: list[str]
) -> tuple[int, int] | None:
    pid_index = _find_header_index(header, "process id")
    if pid_index is None:
        pid_index = _find_header_index(header, "pid")
    memory_index = _find_header_index(header, "memory")
    if pid_index is None or memory_index is None:
        return None
    if pid_index >= len(row) or memory_index >= len(row):
        return None

    pid = _parse_first_number_as_int(row[pid_index])
    memory_mb = _parse_first_number_as_int(row[memory_index])
    if pid is None or memory_mb is None:
        return None
    return pid, memory_mb


def _find_header_index(header: list[str], needle: str) -> int | None:
    for index, value in enumerate(header):
        if needle in value:
            return index
    return None


def _split_table_row(line: str) -> list[str]:
    if "|" not in line:
        return []
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _parse_first_number_as_int(text: str) -> int | None:
    match = re.search(r"\d+(?:\.\d+)?", text)
    if match is None:
        return None
    return round(float(match.group(0)))


def _device_label(device_type: str | None) -> str:
    return "NPU" if device_type == "npu" else "GPU"


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

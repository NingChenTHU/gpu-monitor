import csv
from io import StringIO

from gpu_monitor.config import ServerConfig
from gpu_monitor.collectors.common import (
    clamp_percent,
    map_pid_details,
    parse_snapshot_sections,
)
from gpu_monitor.models import GPUStatus, ProcessInfo, ServerSnapshot
from gpu_monitor.ssh_client import SSHMonitorClient

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


async def collect(
    server: ServerConfig,
    ssh_client: SSHMonitorClient,
    *,
    timeout: float,
) -> ServerSnapshot:
    raw = await ssh_client.run_probe(server, _GPU_SNAPSHOT_PROBE, timeout=timeout)
    sections = parse_snapshot_sections(raw, ("GPU", "APPS", "PS"))
    gpu_rows = _parse_csv_lines("\n".join(sections.get("GPU", [])))
    gpus = [gpu for row in gpu_rows if (gpu := _parse_gpu_row(row)) is not None]

    process_rows = _parse_csv_lines("\n".join(sections.get("APPS", [])))
    processes_by_gpu = _map_process_rows(process_rows, sections.get("PS", []))

    for gpu in gpus:
        gpu.processes = processes_by_gpu.get(gpu.uuid, [])
        gpu.utilization_percent = clamp_percent(gpu.utilization_percent)

    return ServerSnapshot(name=server.host, device_type="gpu", gpus=gpus)


def _parse_csv_lines(raw: str) -> list[list[str]]:
    lines = []
    for row in csv.reader(StringIO(raw), skipinitialspace=True):
        if not row or not any(value.strip() for value in row):
            continue
        lines.append([value.strip() for value in row])
    return lines


def _map_process_rows(
    process_rows: list[list[str]], process_lines: list[str]
) -> dict[str, list[ProcessInfo]]:
    pid_to_gpus: dict[int, list[tuple[str, int]]] = {}
    for row in process_rows:
        try:
            pid = int(row[0])
            gpu_uuid = row[1].strip()
            memory_mb = int(row[2])
            pid_to_gpus.setdefault(pid, []).append((gpu_uuid, memory_mb))
        except (IndexError, ValueError):
            continue
    return map_pid_details(pid_to_gpus, process_lines)


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

    name = row[2]
    display_name = name[7:].strip() if name.lower().startswith("nvidia ") else None
    return GPUStatus(
        index=index,
        uuid=row[1],
        name=name,
        display_name=display_name or None,
        memory_total_mb=memory_total_mb,
        memory_used_mb=memory_used_mb,
        utilization_percent=utilization_percent,
    )

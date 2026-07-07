import re
from collections.abc import Iterable

from gpu_monitor.config import ServerConfig
from gpu_monitor.collectors.common import (
    clamp_percent,
    map_pid_details,
    parse_snapshot_sections,
)
from gpu_monitor.models import GPUStatus, ProcessInfo, ServerSnapshot
from gpu_monitor.ssh_client import SSHMonitorClient

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


async def collect(
    server: ServerConfig,
    ssh_client: SSHMonitorClient,
    *,
    timeout: float,
) -> ServerSnapshot:
    raw = await ssh_client.run_probe(server, _NPU_SNAPSHOT_PROBE, timeout=timeout)
    sections = parse_snapshot_sections(raw, ("NPU", "NPU_PROC", "PS"))
    gpus = _parse_npu_info_lines(sections.get("NPU", []))
    processes_by_npu = _map_npu_process_rows(
        sections.get("NPU_PROC", []),
        sections.get("PS", []),
    )

    for gpu in gpus:
        gpu.processes = processes_by_npu.get(gpu.uuid, [])
        gpu.utilization_percent = clamp_percent(gpu.utilization_percent)

    return ServerSnapshot(name=server.host, device_type="npu", gpus=gpus)


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

    return map_pid_details(pid_to_npus, ps_lines)


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

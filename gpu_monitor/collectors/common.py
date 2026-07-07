from collections.abc import Iterable

from gpu_monitor.models import ProcessInfo


def parse_snapshot_sections(raw: str, section_names: Iterable[str]) -> dict[str, list[str]]:
    sections = {name: [] for name in section_names}
    markers = {f"__{name}__": name for name in sections}
    current: str | None = None
    for line in raw.splitlines():
        marker = line.strip()
        if marker in markers:
            current = markers[marker]
            continue
        if current is not None and line.strip():
            sections[current].append(line)
    return sections


def parse_ps_lines(lines: Iterable[str]) -> dict[int, str]:
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


def map_pid_details(
    pid_to_devices: dict[int, list[tuple[str, int]]],
    ps_lines: Iterable[str],
) -> dict[str, list[ProcessInfo]]:
    process_map: dict[str, list[ProcessInfo]] = {}
    details = parse_ps_lines(ps_lines)
    for pid, user in details.items():
        for device_uuid, memory_mb in pid_to_devices.get(pid, []):
            process_map.setdefault(device_uuid, []).append(
                ProcessInfo(user=user, memory_mb=memory_mb)
            )
    return process_map


def clamp_percent(value: int) -> int:
    return min(max(value, 0), 100)

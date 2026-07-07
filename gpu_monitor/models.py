from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class ProcessInfo:
    user: str
    memory_mb: int


@dataclass(slots=True)
class DeviceStatus:
    index: int
    uuid: str
    name: str
    memory_total_mb: int
    memory_used_mb: int
    utilization_percent: int
    display_name: str | None = None
    processes: list[ProcessInfo] = field(default_factory=list)
    device_type: str = "gpu"


@dataclass(slots=True)
class ServerSnapshot:
    name: str
    last_seen: datetime | None = None
    is_stale: bool = False
    device_type: str | None = None
    devices: list[DeviceStatus] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

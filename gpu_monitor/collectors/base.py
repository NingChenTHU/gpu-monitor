from typing import Protocol

from gpu_monitor.config import ServerConfig
from gpu_monitor.models import ServerSnapshot
from gpu_monitor.ssh_client import SSHMonitorClient


class DeviceCollector(Protocol):
    device_type: str

    async def collect(
        self,
        server: ServerConfig,
        ssh_client: SSHMonitorClient,
        *,
        timeout: float,
    ) -> ServerSnapshot:
        """Collect one server snapshot for this device type."""

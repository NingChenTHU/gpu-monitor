import asyncio
import unittest

from gpu_monitor.config import ServerConfig
from gpu_monitor.gpu_monitor import GPUMonitor

SNAPSHOT_OUTPUT = """__GPU__
0, GPU-a, NVIDIA GeForce RTX 4090, 24564, 1024, 55
__APPS__
123, GPU-a, 512
__PS__
123 alice
"""
NPU_SNAPSHOT_OUTPUT = """__NPU__
+-------------------+-----------------+------------------------------------------------+
| NPU     Name      | Health          | Power(W)    Temp(C)     Hugepages-Usage(page) |
| Chip    Device    | Bus-Id          | AICore(%)   Memory-Usage(MB)  HBM-Usage(MB)   |
+===================+=================+================================================+
| 0       Ascend 910B| OK              | 90.0        42          0    / 0              |
| 0       0         | 0000:01:00.0    | 45          1024 / 65536      2048 / 65536    |
+-------------------+-----------------+------------------------------------------------+
__NPU_PROC__
__NPU_ID__ 0
| NPU ID | Chip ID | Process ID | Process Memory(MB) |
| 0      | 0       | 456        | 2048               |
__PS__
456 bob
"""
SERVER = ServerConfig(host="gpu-a")


class GPUMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_poll_preserves_previous_snapshot_as_stale(self) -> None:
        client = FakeSSHClient()
        monitor = GPUMonitor([SERVER], client)
        await monitor.refresh_snapshot(SERVER.host, force=True)

        client.fail = True
        snapshot = await monitor.refresh_snapshot(SERVER.host, force=True)

        self.assertTrue(snapshot.is_stale)
        self.assertEqual(snapshot.devices[0].uuid, "GPU-a")
        self.assertEqual(
            snapshot.warnings,
            ["Polling failed; showing last known data"],
        )

    async def test_snapshot_collection_maps_process_details_from_sections(self) -> None:
        client = FakeSSHClient()
        monitor = GPUMonitor([SERVER], client)

        snapshot = await monitor.refresh_snapshot(SERVER.host, force=True)

        device = snapshot.devices[0]
        self.assertEqual(snapshot.device_type, "gpu")
        self.assertEqual(device.device_type, "gpu")
        self.assertEqual(device.uuid, "GPU-a")
        self.assertEqual(device.display_name, "GeForce RTX 4090")
        self.assertEqual(device.processes[0].user, "alice")
        self.assertEqual(device.processes[0].memory_mb, 512)
        self.assertFalse(hasattr(device.processes[0], "pid"))
        self.assertFalse(hasattr(device.processes[0], "command"))
        self.assertEqual(len(client.probes), 1)
        self.assertIn("nvidia-smi", client.probes[0])
        self.assertNotIn("npu-smi", client.probes[0])

    async def test_snapshot_collection_uses_npu_probe_for_npu_servers(self) -> None:
        server = ServerConfig(host="npu-a", device_type="npu")
        client = FakeSSHClient(stdout=NPU_SNAPSHOT_OUTPUT)
        monitor = GPUMonitor([server], client)

        snapshot = await monitor.refresh_snapshot(server.host, force=True)

        npu = snapshot.devices[0]
        self.assertEqual(snapshot.device_type, "npu")
        self.assertEqual(npu.device_type, "npu")
        self.assertEqual(npu.uuid, "npu-0")
        self.assertEqual(npu.name, "Ascend 910B")
        self.assertIsNone(npu.display_name)
        self.assertEqual(npu.memory_used_mb, 2048)
        self.assertEqual(npu.memory_total_mb, 65536)
        self.assertEqual(npu.utilization_percent, 45)
        self.assertEqual(npu.processes[0].user, "bob")
        self.assertEqual(npu.processes[0].memory_mb, 2048)
        self.assertEqual(len(client.probes), 1)
        self.assertIn("npu-smi", client.probes[0])
        self.assertNotIn("nvidia-smi", client.probes[0])

    async def test_snapshot_collection_uses_server_connect_timeout_for_probe_timeout(self) -> None:
        server = ServerConfig(host="gpu-a", ssh_options={"ConnectTimeout": 12})
        client = FakeSSHClient()
        monitor = GPUMonitor([server], client)

        await monitor.refresh_snapshot(server.host, force=True)

        self.assertEqual(client.timeouts, [12.0])

    async def test_snapshot_collection_defaults_probe_timeout_below_poll_interval(self) -> None:
        client = FakeSSHClient()
        monitor = GPUMonitor([SERVER], client, poll_interval_seconds=20)

        await monitor.refresh_snapshot(SERVER.host, force=True)

        self.assertEqual(client.timeouts, [19.0])

    async def test_snapshot_collection_caps_default_probe_timeout(self) -> None:
        client = FakeSSHClient()
        monitor = GPUMonitor([SERVER], client, poll_interval_seconds=300)

        await monitor.refresh_snapshot(SERVER.host, force=True)

        self.assertEqual(client.timeouts, [30.0])

    async def test_snapshot_collection_keeps_gpu_data_when_optional_sections_are_missing(self) -> None:
        client = FakeSSHClient(
            stdout="""__GPU__
0, GPU-a, NVIDIA GeForce RTX 4090, 24564, 1024, 55
"""
        )
        monitor = GPUMonitor([SERVER], client)

        snapshot = await monitor.refresh_snapshot(SERVER.host, force=True)

        self.assertEqual(snapshot.devices[0].uuid, "GPU-a")
        self.assertEqual(snapshot.devices[0].processes, [])

    async def test_snapshot_collection_skips_malformed_rows(self) -> None:
        client = FakeSSHClient(
            stdout="""__GPU__
not,a,valid,row
0, GPU-a, NVIDIA GeForce RTX 4090, 24564, 1024, 55
__APPS__
bad,row
123, GPU-a, 512
__PS__
bad
123 alice
"""
        )
        monitor = GPUMonitor([SERVER], client)

        snapshot = await monitor.refresh_snapshot(SERVER.host, force=True)

        self.assertEqual(len(snapshot.devices), 1)
        self.assertEqual(snapshot.devices[0].uuid, "GPU-a")
        self.assertEqual(snapshot.devices[0].processes[0].user, "alice")

    async def test_non_force_refresh_uses_cache_inside_poll_interval(self) -> None:
        client = FakeSSHClient()
        monitor = GPUMonitor([SERVER], client, poll_interval_seconds=20)

        await monitor.refresh_snapshot(SERVER.host)
        await monitor.refresh_snapshot(SERVER.host)

        self.assertEqual(len(client.probes), 1)

    async def test_force_refresh_bypasses_poll_interval_cache(self) -> None:
        client = FakeSSHClient()
        monitor = GPUMonitor([SERVER], client, poll_interval_seconds=20)

        await monitor.refresh_snapshot(SERVER.host)
        await monitor.refresh_snapshot(SERVER.host, force=True)

        self.assertEqual(len(client.probes), 2)

    async def test_single_server_refresh_only_probes_requested_server(self) -> None:
        servers = [ServerConfig(host="gpu-a"), ServerConfig(host="gpu-b")]
        client = FakeSSHClient()
        monitor = GPUMonitor(servers, client)

        snapshot = await monitor.refresh_snapshot("gpu-b", force=True)

        self.assertEqual(snapshot.name, "gpu-b")
        self.assertEqual(client.hosts, ["gpu-b"])

    async def test_single_server_refresh_uses_per_server_cache(self) -> None:
        servers = [ServerConfig(host="gpu-a"), ServerConfig(host="gpu-b")]
        client = FakeSSHClient()
        monitor = GPUMonitor(servers, client, poll_interval_seconds=20)

        await monitor.refresh_snapshot("gpu-a")
        await monitor.refresh_snapshot("gpu-a")
        await monitor.refresh_snapshot("gpu-b")

        self.assertEqual(client.hosts, ["gpu-a", "gpu-b"])

    async def test_single_server_refresh_rejects_unknown_server(self) -> None:
        monitor = GPUMonitor([SERVER], FakeSSHClient())

        with self.assertRaises(KeyError):
            await monitor.refresh_snapshot("missing")

    async def test_concurrent_refreshes_reuse_in_flight_probe_per_server(self) -> None:
        client = FakeSSHClient(delay=0.05)
        monitor = GPUMonitor([SERVER], client)

        await asyncio.gather(
            monitor.refresh_snapshot(SERVER.host, force=True),
            monitor.refresh_snapshot(SERVER.host, force=True),
        )

        self.assertEqual(len(client.probes), 1)


class FakeSSHClient:
    def __init__(
        self,
        *,
        stdout: str = SNAPSHOT_OUTPUT,
        delay: float = 0,
        fail: bool = False,
    ) -> None:
        self.stdout = stdout
        self.delay = delay
        self.fail = fail
        self.probes: list[str] = []
        self.hosts: list[str] = []
        self.timeouts: list[float | None] = []

    async def run_probe(
        self,
        server: ServerConfig,
        probe: str,
        *,
        timeout: float | None = 30.0,
    ) -> str:
        self.probes.append(probe)
        self.hosts.append(server.host)
        self.timeouts.append(timeout)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise TimeoutError
        return self.stdout

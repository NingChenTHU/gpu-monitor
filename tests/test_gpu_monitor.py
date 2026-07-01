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
SERVER = ServerConfig(host="gpu-a")


class GPUMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_poll_preserves_previous_snapshot_as_stale(self) -> None:
        client = FakeSSHClient()
        monitor = GPUMonitor([SERVER], client)
        await monitor._poll_once(SERVER)

        client.fail = True
        await monitor._poll_once(SERVER)

        snapshots = await monitor.get_all_snapshots()
        self.assertTrue(snapshots[0].is_stale)
        self.assertEqual(snapshots[0].gpus[0].uuid, "GPU-a")
        self.assertEqual(
            snapshots[0].warnings,
            ["Polling failed; showing last known GPU data"],
        )

    async def test_snapshot_collection_maps_process_details_from_sections(self) -> None:
        client = FakeSSHClient()
        monitor = GPUMonitor([SERVER], client)

        await monitor._poll_once(SERVER)
        snapshots = await monitor.get_all_snapshots()

        gpu = snapshots[0].gpus[0]
        self.assertEqual(gpu.uuid, "GPU-a")
        self.assertEqual(gpu.processes[0].user, "alice")
        self.assertEqual(gpu.processes[0].memory_mb, 512)
        self.assertFalse(hasattr(gpu.processes[0], "pid"))
        self.assertFalse(hasattr(gpu.processes[0], "command"))
        self.assertEqual(len(client.probes), 1)

    async def test_snapshot_collection_uses_server_connect_timeout_for_probe_timeout(self) -> None:
        server = ServerConfig(host="gpu-a", ssh_options={"ConnectTimeout": 12})
        client = FakeSSHClient()
        monitor = GPUMonitor([server], client)

        await monitor._poll_once(server)

        self.assertEqual(client.timeouts, [12.0])

    async def test_snapshot_collection_keeps_gpu_data_when_optional_sections_are_missing(self) -> None:
        client = FakeSSHClient(
            stdout="""__GPU__
0, GPU-a, NVIDIA GeForce RTX 4090, 24564, 1024, 55
"""
        )
        monitor = GPUMonitor([SERVER], client)

        await monitor._poll_once(SERVER)
        snapshots = await monitor.get_all_snapshots()

        self.assertEqual(snapshots[0].gpus[0].uuid, "GPU-a")
        self.assertEqual(snapshots[0].gpus[0].processes, [])

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

        await monitor._poll_once(SERVER)
        snapshots = await monitor.get_all_snapshots()

        self.assertEqual(len(snapshots[0].gpus), 1)
        self.assertEqual(snapshots[0].gpus[0].uuid, "GPU-a")
        self.assertEqual(snapshots[0].gpus[0].processes[0].user, "alice")


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
        self.timeouts: list[float | None] = []

    async def run_probe(
        self,
        server: ServerConfig,
        probe: str,
        *,
        timeout: float | None = 30.0,
    ) -> str:
        self.probes.append(probe)
        self.timeouts.append(timeout)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise TimeoutError
        return self.stdout

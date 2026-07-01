from contextlib import contextmanager
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from gpu_monitor.config import ServerConfig
from gpu_monitor.ssh_client import SSHMonitorClient

SSH_HOME = Path(__file__).resolve().parent / "fixtures" / "ssh_home"
FAKE_TEMP_ROOT = Path(__file__).resolve().parent / "fixtures" / "generated_ssh_configs"


class SSHMonitorClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_ssh_command_uses_batch_mode_when_no_direct_options(self) -> None:
        captured: dict[str, Any] = {}

        async def fake_create_subprocess_exec(*args: str, **kwargs: Any) -> object:
            captured["args"] = args
            captured["kwargs"] = kwargs
            return FakeProcess(stdout="ok\n")

        with patch(
            "gpu_monitor.ssh_client.asyncio.create_subprocess_exec",
            fake_create_subprocess_exec,
        ):
            server = ServerConfig(host="gpu-a", ssh_options={})
            client = SSHMonitorClient([server])
            result = await client.run_probe(server, "nvidia-smi")

        self.assertEqual(result, "ok\n")
        self.assertEqual(
            captured["args"],
            ("ssh", "-o", "BatchMode=yes", "gpu-a", "nvidia-smi"),
        )
        self.assertIsNotNone(captured["kwargs"]["stdout"])

    async def test_ssh_command_writes_direct_options_to_openssh_config(self) -> None:
        captured: dict[str, Any] = {}

        async def fake_create_subprocess_exec(*args: str, **kwargs: Any) -> object:
            captured["args"] = args
            config_path = args[4]
            captured["config"] = config_path
            captured["config_text"] = config_path.read_text(encoding="utf-8")
            return FakeProcess(stdout="ok\n")

        with (
            patch_temporary_configs(),
            patch("gpu_monitor.ssh_client.Path.home", lambda: SSH_HOME),
            patch(
                "gpu_monitor.ssh_client.asyncio.create_subprocess_exec",
                fake_create_subprocess_exec,
            ),
        ):
            server = ServerConfig(
                host="gpu-a",
                ssh_options={
                    "HostName": "10.0.0.11",
                    "User": "tester",
                    "Port": 6001,
                    "IdentityFile": "C:/Users/tester/.ssh/my key",
                    "ProxyJump": "bastion",
                    "StrictHostKeyChecking": "no",
                },
            )
            client = SSHMonitorClient([server])
            try:
                await client.run_probe(server, "nvidia-smi")
            finally:
                client.close()

        self.assertEqual(
            captured["args"][:5],
            ("ssh", "-o", "BatchMode=yes", "-F", captured["config"]),
        )
        self.assertEqual(captured["args"][5:], ("gpu-a", "nvidia-smi"))
        self.assertIn("Host gpu-a\n", captured["config_text"])
        self.assertIn("    HostName 10.0.0.11\n", captured["config_text"])
        self.assertIn("    User tester\n", captured["config_text"])
        self.assertIn("    Port 6001\n", captured["config_text"])
        self.assertIn(
            '    IdentityFile "C:/Users/tester/.ssh/my key"\n',
            captured["config_text"],
        )
        self.assertIn("    ProxyJump bastion\n", captured["config_text"])
        self.assertIn("    StrictHostKeyChecking no\n", captured["config_text"])
        self.assertIn(
            f'Include "{(SSH_HOME / ".ssh" / "config").as_posix()}"\n',
            captured["config_text"],
        )

    def test_ssh_config_rejects_newlines_in_values(self) -> None:
        with patch_temporary_configs():
            server = ServerConfig(
                host="gpu-a",
                ssh_options={
                    "User": "tester\nProxyCommand bad",
                },
            )

            with self.assertRaisesRegex(ValueError, "Invalid SSH config value"):
                SSHMonitorClient([server])

    async def test_ssh_command_reuses_prepared_config_for_repeated_commands(self) -> None:
        captured_args: list[tuple[Any, ...]] = []

        async def fake_create_subprocess_exec(*args: str, **kwargs: Any) -> object:
            captured_args.append(args)
            return FakeProcess(stdout="ok\n")

        with (
            patch_temporary_configs(),
            patch("gpu_monitor.ssh_client.Path.home", lambda: SSH_HOME),
            patch(
                "gpu_monitor.ssh_client.asyncio.create_subprocess_exec",
                fake_create_subprocess_exec,
            ),
        ):
            server = ServerConfig(
                host="gpu-a",
                ssh_options={
                    "HostName": "10.0.0.11",
                    "User": "tester",
                },
            )
            client = SSHMonitorClient([server])
            try:
                await client.run_probe(server, "nvidia-smi")
                await client.run_probe(server, "uptime")
            finally:
                client.close()

        self.assertEqual(captured_args[0][:4], ("ssh", "-o", "BatchMode=yes", "-F"))
        self.assertEqual(captured_args[1][:4], ("ssh", "-o", "BatchMode=yes", "-F"))
        self.assertEqual(captured_args[0][4], captured_args[1][4])
        self.assertEqual(captured_args[0][5:], ("gpu-a", "nvidia-smi"))
        self.assertEqual(captured_args[1][5:], ("gpu-a", "uptime"))

    async def test_ssh_command_failure_raises_plain_runtime_error(self) -> None:
        async def fake_create_subprocess_exec(*args: str, **kwargs: Any) -> object:
            return FakeProcess(stderr="boom\n", returncode=2)

        with patch(
            "gpu_monitor.ssh_client.asyncio.create_subprocess_exec",
            fake_create_subprocess_exec,
        ):
            server = ServerConfig(host="gpu-a")
            client = SSHMonitorClient([server])
            with self.assertRaises(RuntimeError) as context:
                await client.run_probe(server, "nvidia-smi")

        self.assertIs(type(context.exception), RuntimeError)
        self.assertEqual(
            str(context.exception),
            "SSH probe failed with exit code 2: nvidia-smi",
        )


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
    ) -> None:
        self._stdout = stdout.encode()
        self._stderr = stderr.encode()
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode


@contextmanager
def patch_temporary_configs():
    stored_configs: dict[Path, str] = {}
    user_config = SSH_HOME / ".ssh" / "config"
    original_exists = Path.exists
    original_read_text = Path.read_text
    original_write_text = Path.write_text

    class FakeTemporaryDirectory:
        counter = 0

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            type(self).counter += 1
            self.name = str(FAKE_TEMP_ROOT / str(type(self).counter))

        def cleanup(self) -> None:
            return None

    def fake_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if FAKE_TEMP_ROOT in self.parents:
            return stored_configs[self]
        return original_read_text(self, *args, **kwargs)

    def fake_exists(self: Path) -> bool:
        if self == user_config:
            return True
        return original_exists(self)

    def fake_write_text(self: Path, data: str, *args: Any, **kwargs: Any) -> int:
        if FAKE_TEMP_ROOT in self.parents:
            stored_configs[self] = data
            return len(data)
        return original_write_text(self, data, *args, **kwargs)

    with (
        patch(
            "gpu_monitor.ssh_client.tempfile.TemporaryDirectory",
            FakeTemporaryDirectory,
        ),
        patch.object(Path, "exists", fake_exists),
        patch.object(Path, "read_text", fake_read_text),
        patch.object(Path, "write_text", fake_write_text),
    ):
        yield

import asyncio
import re
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from gpu_monitor.config import ServerConfig


class SSHMonitorClient:
    """Runs GPU monitor probes through the system OpenSSH client."""

    def __init__(self, servers: Iterable[ServerConfig]) -> None:
        self._ssh_args_by_host: dict[str, list[Any]] = {}
        self._temporary_directories: list[tempfile.TemporaryDirectory[str]] = []
        for server in servers:
            self._ssh_args_by_host[server.host] = self._build_ssh_args(server)

    def _build_ssh_args(self, server: ServerConfig) -> list[Any]:
        if not server.ssh_options:
            return ["ssh", "-o", "BatchMode=yes", server.host]

        lines = [f"Host {_format_ssh_config_value(server.host)}"]
        for key, value in server.ssh_options.items():
            lines.append(f"    {_format_ssh_config_key(key)} {_format_ssh_config_value(value)}")

        user_config = Path.home() / ".ssh" / "config"
        if user_config.exists():
            lines.append(f"Include {_format_ssh_config_value(user_config.as_posix(), quote=True)}")

        tmp_dir = tempfile.TemporaryDirectory(prefix="gpu-monitor-ssh-")
        self._temporary_directories.append(tmp_dir)
        config_path = Path(tmp_dir.name) / "config"
        config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return ["ssh", "-o", "BatchMode=yes", "-F", config_path, server.host]

    def close(self) -> None:
        self._ssh_args_by_host.clear()
        while self._temporary_directories:
            self._temporary_directories.pop().cleanup()

    async def run_probe(
        self,
        server: ServerConfig,
        probe: str,
        *,
        timeout: float | None = 30.0,
    ) -> str:
        process = await asyncio.create_subprocess_exec(
            *self._ssh_args_by_host[server.host],
            probe,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise

        if process.returncode != 0:
            raise RuntimeError(f"SSH probe failed with exit code {process.returncode}: {probe}")
        return stdout_bytes.decode(errors="replace")


def _format_ssh_config_key(key: str) -> str:
    if not re.fullmatch(r"^[a-zA-Z][a-zA-Z0-9_-]*$", key):
        raise ValueError(f"Invalid SSH config key: {key}")
    return key


def _format_ssh_config_value(value: Any, *, quote: bool = False) -> str:
    if isinstance(value, bool):
        text = "yes" if value else "no"
    else:
        text = str(value)

    if any(character in text for character in ("\n", "\r", "\0")):
        raise ValueError("Invalid SSH config value")
    if (
        quote
        or not text
        or any(character.isspace() for character in text)
        or any(character in text for character in ('"', "#", "\\"))
    ):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


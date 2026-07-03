import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ServerConfig:
    host: str
    ssh_options: dict[str, Any] = field(default_factory=dict)
    device_type: str = "gpu"


def load_config(path: Path) -> tuple[list[ServerConfig], int]:
    config_path = path.expanduser()
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    servers: list[ServerConfig] = []
    seen_hosts: set[str] = set()
    for item in data.get("servers") or []:
        if not isinstance(item, Mapping) or "Host" not in item:
            raise ValueError("Each server must define Host")
        host = str(item["Host"])
        if host in seen_hosts:
            raise ValueError(f"Duplicate Host in configuration: {host}")
        seen_hosts.add(host)
        device_type = _normalize_device_type(item.get("DeviceType", "gpu"))
        servers.append(
            ServerConfig(
                host=host,
                ssh_options={
                    key: value
                    for key, value in item.items()
                    if key not in {"Host", "DeviceType"}
                },
                device_type=device_type,
            )
        )

    if "poll_interval_seconds" not in data:
        raise ValueError("poll_interval_seconds must be defined")
    try:
        poll_interval_seconds = int(data["poll_interval_seconds"])
    except (TypeError, ValueError) as exc:
        raise ValueError("poll_interval_seconds must be an integer") from exc
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")

    return servers, poll_interval_seconds


def _normalize_device_type(value: Any) -> str:
    device_type = str(value).strip().lower()
    if device_type not in {"gpu", "npu"}:
        raise ValueError("DeviceType must be gpu or npu")
    return device_type

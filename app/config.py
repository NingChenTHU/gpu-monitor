import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "servers.toml"


@dataclass(slots=True)
class ServerConfig:
    host: str
    ssh_options: dict[str, Any] = field(default_factory=dict)


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> tuple[list[ServerConfig], int]:
    config_path = Path(path)
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
        servers.append(
            ServerConfig(
                host=host,
                ssh_options={key: value for key, value in item.items() if key != "Host"},
            )
        )

    if "poll_interval_seconds" not in data:
        raise ValueError("poll_interval_seconds must be defined")
    try:
        poll_interval_seconds = int(data["poll_interval_seconds"])
    except (TypeError, ValueError) as exc:
        raise ValueError("poll_interval_seconds must be an integer") from exc
    if poll_interval_seconds < 5:
        raise ValueError("poll_interval_seconds must be at least 5")

    return servers, poll_interval_seconds

import argparse
from pathlib import Path

SAMPLE_CONFIG = """# GPU Monitor configuration.
#
# All servers share this polling interval.
poll_interval_seconds = 20

# Minimal form: use an existing Host entry from your SSH config.
# Uncomment and edit this block.
# [[servers]]
# Host = "server-a"

# NPU server: set DeviceType to npu.
# [[servers]]
# Host = "npu-server-a"
# DeviceType = "npu"

# Full form: write connection details directly in this file.
# Uncomment and edit this block if you do not use an SSH config entry.
# [[servers]]
# Host = "server-b"
# HostName = "10.0.0.12"
# User = "your_username"
# Port = 22
# IdentityFile = "~/.ssh/id_rsa"
# ConnectTimeout = 5
"""


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return _init_config(args.config, force=args.force)
    if args.command == "run":
        return _run_server(args.config, host=args.host, port=args.port)

    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gpu-monitor")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="run the GPU Monitor web service")
    run_parser.add_argument(
        "-c", "--config", required=True, type=Path, help="path to servers.toml"
    )
    run_parser.add_argument("-H", "--host", default="127.0.0.1", help="host to bind")
    run_parser.add_argument("-p", "--port", type=int, default=8000, help="port to bind")

    init_parser = subparsers.add_parser("init", help="write a sample servers.toml file")
    init_parser.add_argument("-c", "--config", required=True, type=Path, help="path to write")
    init_parser.add_argument(
        "-f", "--force", action="store_true", help="overwrite an existing config file"
    )
    return parser


def _run_server(config: Path, *, host: str, port: int) -> int:
    from gpu_monitor.main import create_app
    import uvicorn

    uvicorn.run(create_app(config), host=host, port=port)
    return 0


def _init_config(config: Path, *, force: bool = False) -> int:
    config_path = config.expanduser()
    if config_path.exists() and not force:
        raise FileExistsError(f"Configuration file already exists: {config_path}")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(SAMPLE_CONFIG, encoding="utf-8")
    return 0

# GPU Monitor

GPU Monitor is a small browser-based dashboard for checking GPU or NPU usage across SSH-accessible servers. It shows accelerator memory usage, utilization, and active compute users without requiring an agent on the target machines.

## Preview

![GPU Monitor screenshot](https://raw.githubusercontent.com/NingChenTHU/gpu-monitor/main/docs/assets/gpu-monitor-screenshot.png)

## Features

- Monitor multiple GPU or NPU servers from one page.
- Show accelerator memory usage, utilization, and active process owners.
- Refresh automatically and provide a manual Refresh button.
- Keep the last known accelerator data visible when a server is temporarily unreachable.
- Update server cards independently, so a slow host does not block the rest of the dashboard.

## Requirements

- Python 3.11 or newer.
- SSH access from the machine running GPU Monitor to each target server.
- NVIDIA drivers and `nvidia-smi` on GPU servers.
- Ascend `npu-smi` on NPU servers.

Before configuring GPU Monitor, verify that SSH can run the monitor command:

```sh
ssh server-a nvidia-smi
ssh npu-server-a npu-smi info
```

## Installation

Install from PyPI:

```sh
python -m pip install gpu-server-monitor
```

## Quick Start

Create a sample configuration file:

```sh
gpu-monitor init -c ./config.toml
```

Edit `config.toml`, then start the web service:

```sh
gpu-monitor run -c ./config.toml -H 127.0.0.1 -p 8000
```

Open:

```text
http://127.0.0.1:8000/
```

## Configuration

GPU Monitor uses a TOML configuration file.

The recommended setup is to keep SSH connection details in your normal SSH config file:

```sshconfig
Host server-a
    HostName 10.0.0.11
    User your_username
    Port 22
    IdentityFile ~/.ssh/id_rsa
```

Then reference the SSH host name from GPU Monitor:

```toml
poll_interval_seconds = 20

[[servers]]
Host = "server-a"

[[servers]]
Host = "npu-server-a"
DeviceType = "npu"
```

You can also put SSH options directly in the GPU Monitor config:

```toml
poll_interval_seconds = 20

[[servers]]
Host = "server-b"
HostName = "10.0.0.12"
User = "your_username"
Port = 22
IdentityFile = "~/.ssh/id_rsa"
ConnectTimeout = 5
```

Add more `[[servers]]` blocks to monitor more machines.
`DeviceType` is optional and defaults to `gpu`; set it to `npu` for Ascend NPU servers.

`poll_interval_seconds` controls the automatic refresh interval. Restart GPU Monitor after changing the configuration file.

## Troubleshooting

### No servers appear

Check that the config file passed to `-c` contains at least one `[[servers]]` block.

### A server shows stale data or cannot be reached

Test the same host from your terminal:

```sh
ssh server-a nvidia-smi
ssh npu-server-a npu-smi info
```

If that command fails, fix the SSH login, key, port, or network issue first.

### Configuration changes do not appear

Restart GPU Monitor after editing the configuration file.

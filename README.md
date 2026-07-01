# GPU Monitor

GPU Monitor is a browser-based tool for checking GPU usage across multiple servers. Configure your servers, start the web service, and open the page to see GPU memory usage, utilization, and active compute processes.

## Preview

![GPU Monitor screenshot](https://raw.githubusercontent.com/NingChenTHU/gpu-monitor/main/docs/assets/gpu-monitor-screenshot.png)

## What It Is For

- Check whether GPUs are currently available across multiple servers.
- View GPU memory usage and utilization.
- See which users and processes are using GPUs.
- Keep the last successful data visible when a server is temporarily unreachable.

## Requirements

Before using GPU Monitor, make sure you have:

- Python 3.11 or newer installed.
- SSH access from this machine to the servers you want to monitor.
- NVIDIA drivers installed on the target servers.
- `nvidia-smi` available on the target servers.

You can verify SSH and GPU access with:

```sh
ssh server-a nvidia-smi
```

Replace `server-a` with your own SSH host name or server address.

## Installation

Install GPU Monitor from PyPI:

```sh
python -m pip install gpu-server-monitor
```

If `python` is not the command for your Python environment, replace it with the interpreter you normally use.

## Server Configuration

Create a configuration file:

```sh
gpu-monitor init -c ./servers.toml
```

Edit `servers.toml` and add the servers you want to monitor.

The recommended approach is to keep connection details in your normal SSH config file, such as `~/.ssh/config` on Linux/macOS or `%USERPROFILE%\.ssh\config` on Windows:

```sshconfig
Host server-a
    HostName 10.0.0.11
    User your_username
    Port 22
    IdentityFile ~/.ssh/id_rsa
```

Then `servers.toml` only needs the SSH host name:

```toml
poll_interval_seconds = 20

[[servers]]
Host = "server-a"
```

You can also put connection details directly in `servers.toml`:

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

To monitor more servers, add more `[[servers]]` blocks.

## Running

Run GPU Monitor with:

```sh
gpu-monitor run -c ./servers.toml -H 127.0.0.1 -p 8000
```

Then open this address in your browser:

```text
http://127.0.0.1:8000/
```

## Troubleshooting

### No servers appear on the page

Check that the `servers.toml` passed to `-c` contains at least one `[[servers]]` block and that each `Host` value is spelled correctly.

### A server cannot be reached

First test the connection from your terminal:

```sh
ssh server-a nvidia-smi
```

If this command fails, fix the SSH login, key, port, or network issue first.

### Configuration changes do not appear

Restart GPU Monitor after editing `servers.toml`.

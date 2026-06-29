# GPU Monitor

GPU Monitor is a browser-based tool for checking GPU usage across multiple servers. After you configure your servers, you can open the web page to see each GPU's memory usage, utilization, and active compute processes.

## What It Is For

- Check whether GPUs are currently available across multiple servers.
- View GPU memory usage and utilization.
- See which users and processes are using GPUs.
- Keep the last successful data visible when a server is temporarily unreachable, with a stale-data warning.

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

Run this command from the project directory:

```sh
python -m pip install -e .
```

If `python` is not the command for your Python environment, replace it with the interpreter you normally use.

## Server Configuration

Edit `servers.toml` in the project root to configure the servers you want to monitor.

GPU Monitor runs SSH in non-interactive mode automatically, so you do not need to set `BatchMode` yourself. Each probe uses a 5-second timeout by default. Set `ConnectTimeout` for a server if it needs a different timeout.

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

You can also put the connection details directly in `servers.toml`:

```toml
poll_interval_seconds = 20

[[servers]]
Host = "server-b"
HostName = "10.0.0.12"
User = "your_username"
Port = 22
IdentityFile = "~/.ssh/id_rsa"
```

To monitor more servers, add more `[[servers]]` blocks.

## Running

Run this command from the project directory:

```sh
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open this address in your browser:

```text
http://127.0.0.1:8000/
```

## Page Guide

The page groups GPU status by server. Common fields include:

- Memory: used GPU memory and total GPU memory.
- Utilization: current GPU compute utilization.
- Processes: active GPU processes, including user and memory usage when available.
- Stale status: shown when the latest server check failed and the page is displaying the last successful data.

## Troubleshooting

### No servers appear on the page

Check that `servers.toml` contains at least one `[[servers]]` block and that the `Host` value is spelled correctly.

### A server cannot be reached

First test the connection from your terminal:

```sh
ssh server-a nvidia-smi
```

If this command fails, fix the SSH login, key, port, or network issue first.

### Configuration changes do not appear

Restart GPU Monitor after editing `servers.toml`.
